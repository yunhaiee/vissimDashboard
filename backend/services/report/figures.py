"""Track R unit R-2 — four table-figure generators
(docs/product/report-engine-design-2026-08-24.md, layer 2's first four rows;
``services/report/spine.py`` is layer 1 and owns the section ids these
figures fill).

WHAT A FIGURE IS
-----------------
A JSON-safe dict, one of two shapes:

* a real figure — ``{figure_id, title (ko/en/ja), columns (ko/en/ja headers),
  rows, source_caption, spine_section_id, citation}``. ``source_caption`` is
  MANDATORY on every real figure (the design doc's own rule, quoted in its
  layer 2: "이 그림은 어느 실행의 것인가"에 답 못 하는 도판은 방출하지 않는다) —
  it always carries the same five keys (``run_id``, ``session_id``,
  ``dataset_revision``, ``generated_at``, ``reader``) so a caller can check
  completeness without a per-figure branch; a value legitimately being
  ``None`` (a figure with no run context has no ``run_id``) is different from
  the KEY being absent, and only the key's presence is the contract here.
* a NAMED ABSENCE — ``{figure_id, spine_section_id, absent: True, reason
  (ko/en/ja)}``. Never an empty ``rows: []`` pretending to be a real,
  evidenced figure (this repo's ``Number(null)->0`` scar, generalised to
  tables): a reader returning "no source" and a reader returning "a real,
  empty result" are different fassertion answers and must render differently.
  An EXISTING-BUT-EMPTY result (a session whose ledger recorded zero changes,
  a network with zero driving behaviours) is the second kind and returns a
  real figure with ``rows: []`` — the absence marker is reserved for "there
  was nothing to even ask".

Row VALUES are never trilingual. Only the figure's shell — ``title`` and each
column's header, plus an absent figure's ``reason`` — is ko/en/ja; a row cell
is the underlying reader's own value (a number, an id, a verbatim string),
because inventing three renderings of one data point is exactly the kind of
second transcription ``services/report/spine.py``'s docstring warns against.

READERS REUSED, NEVER RE-IMPLEMENTED
-------------------------------------
Every generator's docstring below names the exact function that does the
actual read; no SQL and no file parsing happens in this module. Two of the
four generators (:func:`table_5_6_applied_table`, :func:`gate_verdict_summary`)
need a few lines of glue beyond the bare reader call — "which network is the
active dataset's" and "which generation job gated it" — and that glue
DELIBERATELY MIRRORS ``routers/calibration.py``'s ``_shadow_model`` /
``_applied_rows`` / ``_gate_for_active_dataset``, byte-for-semantic-identical,
because ``services/`` may not import ``routers/`` (the one-way layering this
codebase keeps everywhere else — routers import services, never the reverse;
verified by grep, zero hits the other way). The alternative — promoting that
glue to a shared services function, or having ``routers/report.py`` import
those private router helpers the way ``routers/calibration.py`` already
imports ``routers/genesis.py``'s and ``routers/health.py``'s — would also
have worked; this module takes the small, local duplication instead because
it keeps every generator independently callable (and independently testable
by monkeypatching the READER it names) without a second file doing the
gathering. The DATA each glue path reaches — ``get_active_dataset``,
``list_jobs``, ``gate_payload``, ``build_shadow_model``, ``verify_applied_row``
— is never re-read a second way; only the "which one" filter is repeated, and
it is repeated VERBATIM (same field name, same comparison, same fallback to
``None``).

GENERATED_AT
------------
``datetime.now(timezone.utc).isoformat()`` — the exact call
``services/agent/reality_check.py`` and ``services/genesis_jobs.py``'s
``_now_iso`` both use. Timezone-aware always; a naive timestamp cannot say
which run of a DST-observing clock it was stamped from, which is precisely
the kind of "which run is this" question a source caption exists to answer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services import audit_trail, datasets, genesis_jobs, validation
from services.agent import mrwa_reference, saturation_flow, tools_network
from services.report import spine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spine section ids — validated against R-1's own declaration, never re-typed
# blind. A typo here must fail loudly (an invented section id is exactly the
# invented-capability claim ``spine.build_spine`` refuses for the same
# reason), so this indexes the REAL ids rather than hard-coding a set that
# could drift from spine.py silently.
# ---------------------------------------------------------------------------

_SPINE_SECTION_IDS = {section.id for section in spine.REGULATION_SECTIONS}


def _section(section_id: str) -> str:
    if section_id not in _SPINE_SECTION_IDS:
        raise KeyError(
            f"report figure names spine section {section_id!r}, which "
            f"services.report.spine does not declare (it has: {sorted(_SPINE_SECTION_IDS)})"
        )
    return section_id


SECTION_STATISTICAL_COMPARISON = _section("statistical_comparison")
SECTION_PARAMETER_ADJUSTMENT_RECORD = _section("parameter_adjustment_record")
SECTION_CALIBRATION = _section("calibration")
SECTION_LIMITATIONS = _section("limitations")


# ---------------------------------------------------------------------------
# Shared shape builders
# ---------------------------------------------------------------------------


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_dataset_revision() -> str | None:
    """The active dataset's revision, or ``None`` when it cannot be read.

    Reuses :func:`services.datasets.get_active_dataset` — the same accessor
    every other reader of "which network is active" already calls — and never
    raises: a caption field that cannot be filled is ``None``, not a 500 for
    the whole figure.
    """
    try:
        revision = datasets.get_active_dataset().get("revision")
    except Exception as exc:  # noqa: BLE001 — a caption field, not a fault
        logger.warning("report.figures: active dataset unreadable (%s: %s)", type(exc).__name__, exc)
        return None
    return str(revision) if revision else None


def _caption(
    *, run_id: str | None = None, session_id: str | None = None, reader: str
) -> dict[str, Any]:
    """The mandatory caption block, with a fixed key set on every real figure.

    ``reader`` is the dotted path of the function that actually produced the
    data, stated so a reviewer never has to re-derive it from this module's
    prose.
    """
    return {
        "run_id": run_id,
        "session_id": session_id,
        "dataset_revision": _active_dataset_revision(),
        "generated_at": _generated_at(),
        "reader": reader,
    }


def _reason(ko: str, en: str, ja: str) -> dict[str, str]:
    return {"ko": ko, "en": en, "ja": ja}


def _absent(figure_id: str, section_id: str, reason: dict[str, str]) -> dict[str, Any]:
    return {
        "figure_id": figure_id,
        "spine_section_id": section_id,
        "absent": True,
        "reason": reason,
    }


def _figure(
    *,
    figure_id: str,
    section_id: str,
    title: dict[str, str],
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    source_caption: dict[str, Any],
    citation: str | None,
) -> dict[str, Any]:
    return {
        "figure_id": figure_id,
        "spine_section_id": section_id,
        "absent": False,
        "title": title,
        "columns": columns,
        "rows": rows,
        "source_caption": source_caption,
        "citation": citation,
    }


# ---------------------------------------------------------------------------
# 1. GEH 노드 표 — statistical_comparison
# ---------------------------------------------------------------------------

GEH_NODE_TABLE_ID = "geh_node_table"

_GEH_TITLE = {
    "ko": "GEH 노드 표",
    "en": "GEH by node",
    "ja": "GEHノード表",
}
_GEH_COLUMNS = [
    {"key": "node_id", "ko": "노드", "en": "Node", "ja": "ノード"},
    {"key": "node_name", "ko": "이름", "en": "Name", "ja": "名称"},
    {"key": "observed", "ko": "실측", "en": "Observed", "ja": "実測"},
    {"key": "modelled", "ko": "모델", "en": "Modelled", "ja": "モデル"},
    {"key": "geh", "ko": "GEH", "en": "GEH", "ja": "GEH"},
    {"key": "pass_fail", "ko": "판정", "en": "Pass/Fail", "ja": "判定"},
]
_GEH_READER = "services.agent.tools_metrics.build_reality_check_report"


def geh_node_table(
    scenario_id: str | None,
    *,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
) -> dict[str, Any]:
    """The reality-check report's per-node GEH table (statistical_comparison).

    Calls :func:`services.agent.tools_metrics.build_reality_check_report`
    directly — the same function ``routers/calibration._reality_check``
    wraps for the calibration screen — with no ``targets``: a target is only
    read to decide whether an EDIT could be proposed, and this figure reports
    the comparison, not a proposal, so omitting it costs nothing (confirmed by
    reading ``services/agent/fixes.map_node_counts``: ``node_total_geh`` is
    computed from ``observed_total``/``modelled_total`` before ``targets`` is
    ever consulted).

    Pass/fail reuses :func:`services.validation.geh_grade` (the ONE GEH-band
    function every other caller in this backend shares) rather than a
    threshold comparison written fresh here — "good" is PASS, "fair"/"poor"
    is FAIL, ``None`` (an undefined GEH — see ``validation.geh``'s own
    docstring) is reported as ``"UNDEFINED"``, never silently folded into
    either side.

    NO CRITICAL-INTERSECTION COLUMN: measured, not assumed absent.
    ``services.agent.reality_check`` / ``services.agent.fixes`` /
    ``services.agent.geh_ladder`` carry no "critical intersection" concept at
    all (grepped; zero hits) — that marking exists only in the OTHER,
    parallel GEH path this build has, ``services.report_exports.flow_geh_verdict``
    /``criteria_coverage_rows``, which grades off the counts workbook's own
    (potentially stale) model column — precisely the column
    ``services/agent/reality_check.py``'s module docstring explains this
    reader refuses to use. Mixing the two would print two different GEH
    numbers for the same node under one heading, so this figure does not
    reach for the other path's marking; the design doc's "if present" is
    honoured by leaving the column out rather than fabricating it.

    ``scenario_id`` absent, or the report's own ``status`` is
    ``reality_check.NOT_CHECKABLE`` (no run, no observed counts, multi-seed,
    …) -> a NAMED ABSENCE whose reason is the report's own ``message``,
    carried verbatim rather than re-worded.

    The READER CALL ITSELF is guarded: an exception out of
    ``build_reality_check_report`` (a corrupt result DB, an unreadable
    workbook the reader does not itself catch, …) becomes a named absence
    whose reason names the exception class and message, rather than a bare
    500 that takes the other three figures down with it — ``all_figures``
    gathers this one FIRST, and a crash here must not stop the other three
    from being gathered. That reason is deliberately worded differently from
    every "no evidence" reason above it: a reader that raised is not the same
    fact as a reader that ran and found nothing, and a reader of this figure
    must be able to tell the two apart.
    """
    if not scenario_id:
        return _absent(
            GEH_NODE_TABLE_ID,
            SECTION_STATISTICAL_COMPARISON,
            _reason(
                "실행(scenario_id)이 지정되지 않아 GEH 표를 만들 수 없습니다.",
                "No scenario_id was supplied, so no GEH table can be built.",
                "実行(scenario_id)が指定されていないため、GEH表を作成できません。",
            ),
        )

    # Deferred like every other agent-tool import at this layer: importing
    # tools_metrics at module scope pulls the whole tool registry in at
    # import time and can reorder tool registration (the exact concern
    # routers/calibration._reality_check's own comment states).
    from services.agent.tools_metrics import build_reality_check_report

    try:
        report = build_reality_check_report(
            scenario_id,
            period=period or None,
            targets=None,
            observation_set_id=observation_set_id or None,
            observation_period=observation_period or None,
        )
    except Exception as exc:  # noqa: BLE001 — a crashed reader is a named absence, not a 500
        logger.warning(
            "report.figures: build_reality_check_report raised (%s: %s)", type(exc).__name__, exc
        )
        return _absent(
            GEH_NODE_TABLE_ID,
            SECTION_STATISTICAL_COMPARISON,
            _reason(
                f"GEH 표를 만드는 리더가 오류를 내며 실패했습니다 ({type(exc).__name__}: {exc}).",
                f"The reader that builds the GEH table raised an error ({type(exc).__name__}: {exc}).",
                f"GEH表を作成するリーダーがエラーで失敗しました ({type(exc).__name__}: {exc})。",
            ),
        )

    if report.get("status") != "CHECKED":
        return _absent(GEH_NODE_TABLE_ID, SECTION_STATISTICAL_COMPARISON, dict(report.get("message") or {}))

    rows: list[dict[str, Any]] = []
    for node in report.get("nodes") or []:
        geh_value = node.get("node_total_geh")
        grade = validation.geh_grade(geh_value)
        pass_fail = "UNDEFINED" if grade is None else ("PASS" if grade == "good" else "FAIL")
        rows.append(
            {
                "node_id": node.get("node_id"),
                "node_name": node.get("node_name"),
                "observed": node.get("observed_total"),
                "modelled": node.get("modelled_total"),
                "geh": geh_value,
                "pass_fail": pass_fail,
            }
        )

    return _figure(
        figure_id=GEH_NODE_TABLE_ID,
        section_id=SECTION_STATISTICAL_COMPARISON,
        title=_GEH_TITLE,
        columns=_GEH_COLUMNS,
        rows=rows,
        source_caption=_caption(run_id=scenario_id, reader=_GEH_READER),
        citation=spine.CIT_2_12_2_STATISTICAL_COMPARISON,
    )


# ---------------------------------------------------------------------------
# 2. 승인 이력 표 — parameter_adjustment_record
# ---------------------------------------------------------------------------

APPROVAL_HISTORY_TABLE_ID = "approval_history_table"

_APPROVAL_TITLE = {
    "ko": "승인 이력 표",
    "en": "Approval history",
    "ja": "承認履歴表",
}
_APPROVAL_COLUMNS = [
    {"key": "seq", "ko": "#", "en": "#", "ja": "#"},
    {"key": "when", "ko": "시각(UTC)", "en": "Time (UTC)", "ja": "時刻(UTC)"},
    {"key": "who", "ko": "승인자", "en": "Approver", "ja": "承認者"},
    {"key": "what", "ko": "항목", "en": "What changed", "ja": "変更内容"},
    {"key": "reason", "ko": "근거", "en": "Rationale", "ja": "根拠"},
]
_APPROVAL_READER = "services.audit_trail.read_ledger"


def approval_history_table(session_id: str | None) -> dict[str, Any]:
    """One agent session's APPLIED parameter changes (parameter_adjustment_record).

    Calls :func:`services.audit_trail.read_ledger` directly — the exact
    reader ``routers/agent_audit.py``'s existing DOCX/summary export already
    uses, precedent named in this unit's brief. Only ``ledger.changes`` is
    rendered: the spine's citation for this section is §2.12.2's "adjustments
    made to parameters ... should be documented" — refusals/rollbacks are not
    adjustments and belong to ``audit_trail``'s own Table 2, not this figure.

    ``who`` is the ledger's ``Approver`` dataclass, verbatim as a dict (or
    ``None`` for a pre-authentication / unauthenticated record) — never the
    DOCX's localized "display_name (username · role-label)" composite, because
    a role LABEL is language-bound prose and this module's row values are not
    (see the module docstring). ``reason`` is ``entry.reason`` untouched: the
    unit's mutation guard is exactly "paraphrased instead of verbatim dies".

    ``session_id`` absent, or :func:`read_ledger` returns ``None`` (no such
    session / no ledger file) -> NAMED ABSENCE. A session that exists and
    simply recorded zero changes is NOT an absence — ``audit_trail`` itself
    treats "this session changed nothing" as a real, empty-state answer
    (``AuditLedger.is_empty``), so this figure returns a real, empty-rows
    table for that case instead.

    The READER CALL is guarded the same way :func:`geh_node_table`'s is: an
    exception out of :func:`read_ledger` (a torn ledger file the reader's own
    per-line skip does not cover, a workspace-resolution error, …) becomes a
    named absence naming the exception, not a bare 500 — ``all_figures``
    gathers this figure SECOND, right after the GEH table, and a crash here
    must not stop the Table 5-6 and gate figures from being gathered either.
    """
    if not session_id:
        return _absent(
            APPROVAL_HISTORY_TABLE_ID,
            SECTION_PARAMETER_ADJUSTMENT_RECORD,
            _reason(
                "세션(session_id)이 지정되지 않아 승인 이력을 불러올 수 없습니다.",
                "No session_id was supplied, so no approval history can be loaded.",
                "セッション(session_id)が指定されていないため、承認履歴を読み込めません。",
            ),
        )

    try:
        ledger = audit_trail.read_ledger(session_id)
    except Exception as exc:  # noqa: BLE001 — a crashed reader is a named absence, not a 500
        logger.warning("report.figures: read_ledger raised (%s: %s)", type(exc).__name__, exc)
        return _absent(
            APPROVAL_HISTORY_TABLE_ID,
            SECTION_PARAMETER_ADJUSTMENT_RECORD,
            _reason(
                f"승인 이력을 읽는 리더가 오류를 내며 실패했습니다 ({type(exc).__name__}: {exc}).",
                f"The reader that loads the approval history raised an error "
                f"({type(exc).__name__}: {exc}).",
                f"承認履歴を読み込むリーダーがエラーで失敗しました ({type(exc).__name__}: {exc})。",
            ),
        )
    if ledger is None:
        return _absent(
            APPROVAL_HISTORY_TABLE_ID,
            SECTION_PARAMETER_ADJUSTMENT_RECORD,
            _reason(
                f"세션 '{session_id}'에 대한 액션 레저가 없습니다.",
                f"There is no action ledger for session '{session_id}'.",
                f"セッション '{session_id}' のアクション台帳がありません。",
            ),
        )

    rows: list[dict[str, Any]] = []
    for entry in ledger.changes:
        who = (
            None
            if entry.approver is None
            else {
                "user_id": entry.approver.user_id,
                "username": entry.approver.username,
                "display_name": entry.approver.display_name,
                "role": entry.approver.role,
            }
        )
        rows.append(
            {
                "seq": entry.seq,
                "when": entry.when,
                "who": who,
                "what": f"{entry.tool}: {entry.summary}",
                "reason": entry.reason,
            }
        )

    return _figure(
        figure_id=APPROVAL_HISTORY_TABLE_ID,
        section_id=SECTION_PARAMETER_ADJUSTMENT_RECORD,
        title=_APPROVAL_TITLE,
        columns=_APPROVAL_COLUMNS,
        rows=rows,
        source_caption=_caption(session_id=session_id, reader=_APPROVAL_READER),
        citation=spine.CIT_2_12_2_PARAMETER_RECORD,
    )


# ---------------------------------------------------------------------------
# 3. Table 5-6 적용 표 — calibration
# ---------------------------------------------------------------------------

TABLE_5_6_APPLIED_ID = "table_5_6_applied"

_TABLE_5_6_TITLE = {
    "ko": "Table 5-6 적용 표",
    "en": "Table 5-6 application",
    "ja": "Table 5-6 適用表",
}
_TABLE_5_6_COLUMNS = [
    {"key": "behavior_no", "ko": "행동 번호", "en": "Behaviour no.", "ja": "行動番号"},
    {"key": "behavior_name", "ko": "행동 이름", "en": "Behaviour name", "ja": "行動名"},
    {"key": "ax", "ko": "ax", "en": "ax", "ja": "ax"},
    {"key": "bx_add", "ko": "bx_add", "en": "bx_add", "ja": "bx_add"},
    {"key": "bx_mult", "ko": "bx_mult", "en": "bx_mult", "ja": "bx_mult"},
    {"key": "matched_row_pcu", "ko": "일치 행(pcu)", "en": "Matched row (pcu)", "ja": "一致行(pcu)"},
    {"key": "standstill_verdict", "ko": "정지거리 판정", "en": "Standstill verdict", "ja": "停止距離判定"},
    {"key": "status", "ko": "상태", "en": "Status", "ja": "状態"},
]
_TABLE_5_6_READER = (
    "services.agent.tools_network.build_shadow_model + "
    "services.agent.saturation_flow.verify_applied_row"
)


def table_5_6_applied_table() -> dict[str, Any]:
    """Every driving behaviour vs MRWA Table 5-6 (calibration).

    Reads the active dataset's ``.inpx`` the same way
    ``routers/calibration._shadow_model`` does (``services.datasets.
    get_active_inpx_path`` -> ``services.agent.tools_network.
    build_shadow_model``, mtime-cached, no COM) and grades each behaviour with
    :func:`services.agent.saturation_flow.verify_applied_row` — #44's verdict,
    UNMODIFIED, one call per behaviour, exactly what
    ``routers/calibration._applied_rows`` does. No behaviour verdict is
    re-derived here.

    No readable ``.inpx`` (no dataset, or an unparseable one) -> NAMED
    ABSENCE, reusing the SAME reason code
    ``routers/calibration.get_applied_rows`` already answers with
    (``NO_READABLE_NETWORK``), so a client that already knows that code from
    the calibration screen recognises it here too. A network that parses but
    names zero driving behaviours is NOT an absence: it is a real, empty
    table.
    """
    try:
        path = datasets.get_active_inpx_path()
    except Exception as exc:  # noqa: BLE001 — no dataset configured at all
        logger.info("report.figures: no active inpx path (%s: %s)", type(exc).__name__, exc)
        path = None

    shadow: dict[str, Any] | None = None
    if path is not None and path.is_file():
        try:
            shadow = tools_network.build_shadow_model(path)
        except Exception as exc:  # noqa: BLE001 — an unparseable .inpx is evidence, not a 500
            logger.warning(
                "report.figures: shadow model unavailable (%s: %s)", type(exc).__name__, exc
            )
            shadow = None

    behaviors = shadow.get("driving_behaviors") if isinstance(shadow, dict) else None
    if not isinstance(behaviors, list):
        return _absent(
            TABLE_5_6_APPLIED_ID,
            SECTION_CALIBRATION,
            _reason(
                "읽을 수 있는 네트워크(.inpx)가 없어 Table 5-6 적용 표를 만들 수 없습니다"
                " (NO_READABLE_NETWORK).",
                "There is no readable network (.inpx), so no Table 5-6 application table can be "
                "built (NO_READABLE_NETWORK).",
                "読み取れるネットワーク(.inpx)がないため、Table 5-6 適用表を作成できません"
                "(NO_READABLE_NETWORK)。",
            ),
        )

    rows: list[dict[str, Any]] = []
    for behavior in behaviors:
        if not isinstance(behavior, dict):
            continue
        verdict = saturation_flow.verify_applied_row(behavior)
        applied = verdict.get("applied") or {}
        row = verdict.get("row")
        rows.append(
            {
                "behavior_no": behavior.get("no"),
                "behavior_name": behavior.get("name"),
                "ax": applied.get("ax"),
                "bx_add": applied.get("bx_add"),
                "bx_mult": applied.get("bx_mult"),
                "matched_row_pcu": None if not row else row.get("saturation_flow_pcu"),
                "standstill_verdict": verdict.get("standstill_matches_guideline"),
                "status": verdict.get("status"),
            }
        )

    return _figure(
        figure_id=TABLE_5_6_APPLIED_ID,
        section_id=SECTION_CALIBRATION,
        title=_TABLE_5_6_TITLE,
        columns=_TABLE_5_6_COLUMNS,
        rows=rows,
        source_caption=_caption(reader=_TABLE_5_6_READER),
        citation=mrwa_reference.TABLE_5_6_CITATION,
    )


# ---------------------------------------------------------------------------
# 4. 게이트 판정 요약 — limitations
# ---------------------------------------------------------------------------

GATE_VERDICT_SUMMARY_ID = "gate_verdict_summary"

_GATE_TITLE = {
    "ko": "게이트 판정 요약",
    "en": "Gate verdict summary",
    "ja": "ゲート判定サマリー",
}
_GATE_COLUMNS = [
    {"key": "check_id", "ko": "점검", "en": "Check", "ja": "チェック"},
    {"key": "tier", "ko": "등급", "en": "Tier", "ja": "階層"},
    {"key": "state", "ko": "판정", "en": "State", "ja": "判定"},
    {"key": "finding_count", "ko": "소견 건수", "en": "Findings", "ja": "所見件数"},
    {"key": "warning_count", "ko": "경고 건수", "en": "Warnings", "ja": "警告件数"},
    {"key": "gap_count", "ko": "공백 건수", "en": "Gaps", "ja": "ギャップ件数"},
    {"key": "attestation_basis", "ko": "현시도 확인 근거", "en": "Attestation basis", "ja": "現示確認根拠"},
]
_GATE_READER = "services.genesis_jobs.list_jobs + services.genesis_jobs.gate_payload"


def gate_verdict_summary(*, language: str = "ko") -> dict[str, Any]:
    """The active dataset's V1-V9 gate verdict, one row per check (limitations).

    Resolution mirrors ``routers/calibration._gate_for_active_dataset``
    exactly (matched on ``publication.dataset_revision``, never on "the
    newest job" — that router's own docstring explains why: a dataset
    revision match is the only rule that cannot attach one generation's
    verdict to a different network that happens to be active). The actual
    reads are :func:`services.datasets.get_active_dataset`,
    :func:`services.genesis_jobs.list_jobs` and
    :func:`services.genesis_jobs.gate_payload` — all reused untouched; only
    the "which job" filter is repeated here because ``services/`` may not
    import ``routers/`` (see the module docstring).

    ``attestation_basis`` is populated on V1's row ONLY, from
    ``gate_payload(...)["live_evidence"]["basis"]`` when present — the exact
    field ``genesis_jobs._gate_summary`` already stamps
    (``"operator_attested"`` vs ``genesis_jobs.LIVE_CHECK_BASIS``) — and is
    ``None`` on every other row, and on V1 itself when V1 rests on no live
    evidence at all (no live-evidence object was ever offered or produced).

    No dataset revision, no job whose publication matches it, or the stored
    gate result cannot be read -> NAMED ABSENCE: a report with no gate to
    show is a limitation, and V1-V9 findings/refusals are exactly what the
    spine's ``limitations`` section (source key ``named_limitations`` +
    ``gate_report``) exists to carry.
    """
    revision = _active_dataset_revision()
    gate: dict[str, Any] | None = None
    if revision:
        try:
            jobs = genesis_jobs.list_jobs()
        except Exception as exc:  # noqa: BLE001
            logger.warning("report.figures: genesis jobs unreadable (%s: %s)", type(exc).__name__, exc)
            jobs = []
        for job in jobs:
            publication = job.get("publication") or {}
            if str(publication.get("dataset_revision") or "") != revision:
                continue
            try:
                gate = genesis_jobs.gate_payload(str(job.get("id") or ""), language=language)
            except Exception as exc:  # noqa: BLE001 — an ungated job is not an error here
                logger.info("report.figures: gate unavailable for job (%s: %s)", type(exc).__name__, exc)
                gate = None
            break

    if gate is None:
        return _absent(
            GATE_VERDICT_SUMMARY_ID,
            SECTION_LIMITATIONS,
            _reason(
                "활성 데이터셋을 통과시킨 생성 작업의 게이트 결과를 찾을 수 없습니다.",
                "No gate result could be found for the generation that published the active "
                "dataset.",
                "アクティブなデータセットを発行した生成ジョブのゲート結果が見つかりません。",
            ),
        )

    live_evidence = gate.get("live_evidence") or {}
    basis = live_evidence.get("basis")

    rows: list[dict[str, Any]] = []
    for check in gate.get("checks") or []:
        rows.append(
            {
                "check_id": check.get("id"),
                "tier": check.get("tier"),
                "state": check.get("state"),
                "finding_count": len(check.get("findings") or []) + int(check.get("findings_omitted") or 0),
                "warning_count": len(check.get("warnings") or []) + int(check.get("warnings_omitted") or 0),
                "gap_count": len(check.get("gaps") or []),
                "attestation_basis": basis if check.get("id") == "V1" else None,
            }
        )

    return _figure(
        figure_id=GATE_VERDICT_SUMMARY_ID,
        section_id=SECTION_LIMITATIONS,
        title=_GATE_TITLE,
        columns=_GATE_COLUMNS,
        rows=rows,
        source_caption=_caption(run_id=str(gate.get("job_id") or "") or None, reader=_GATE_READER),
        citation=spine.CIT_2_12_2_LIMITATIONS,
    )


# ---------------------------------------------------------------------------
# All four, gathered — the shape routers/report.py serves
# ---------------------------------------------------------------------------


def all_figures(
    *,
    scenario_id: str | None = None,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
    session_id: str | None = None,
    language: str = "ko",
) -> list[dict[str, Any]]:
    """The four figures, each gathered independently — the same "degrade one,
    not all" shape ``routers/calibration.get_flow_rail`` uses for its own
    evidence bundle. A caller missing ``session_id`` still gets three real
    figures and one named absence, never a 4xx for the whole set.
    """
    return [
        geh_node_table(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
        ),
        approval_history_table(session_id),
        table_5_6_applied_table(),
        gate_verdict_summary(language=language),
    ]


__all__ = [
    "APPROVAL_HISTORY_TABLE_ID",
    "GATE_VERDICT_SUMMARY_ID",
    "GEH_NODE_TABLE_ID",
    "TABLE_5_6_APPLIED_ID",
    "all_figures",
    "approval_history_table",
    "gate_verdict_summary",
    "geh_node_table",
    "table_5_6_applied_table",
]

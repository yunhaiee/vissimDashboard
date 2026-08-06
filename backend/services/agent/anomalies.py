"""The anomaly catalogue (Phase 2 unit U3) — roadmap row 2.1's four named
anomalies, indexed onto the constraints that already detect them.

What this module is
-------------------
An **indirection**, not a detector. Roadmap row 2.1 names four anomalies —
유령 대기열 (ghost queue), 차단 커넥터 (blocked connector), 기아 링크 (starved
link), diffusion/latent demand > 1% — and this module answers the question
those names raise: *what can this system actually detect, and what is it
blind to?* It adds **no new detection logic**: every check it can run is an
already-registered ``services.agent.constraints`` constraint. What it adds
is addressability (an anomaly name → the constraint ids that speak to it)
and, more importantly, **explicit gaps** where no constraint speaks to one
at all.

Built for two callers, not one:

* **row 2.1** (보정 루프 v1) — anomaly → fix mapping → approval loop. It
  already has postrun findings in hand from ``GuardrailEngine.postrun`` and
  wants them indexed: :func:`index_postrun_findings`.
* **row 3.7** (능동 진단, "flag gridlock/anomalies without being asked") —
  it has only a finished run's payload and no agent turn at all:
  :func:`scan_run_result` runs the postrun catalog itself and indexes the
  result. Row 3.7 is specified to reuse this catalogue verbatim, so the
  entry point takes a bare ``run_result`` and needs nothing else.

The blind-spot rule
-------------------
This is the point of the unit. A scan reports one of three outcomes per
anomaly, and the third is the one that matters:

* ``DETECTED`` — an indexed constraint fired.
* ``CHECKED_CLEAN`` — every indexed constraint ran against real evidence
  and none fired.
* ``NOT_CHECKABLE`` — the check could not be performed, **and the report
  says why**.

``ARMED_BUT_EMPTY`` evidence (unit U1's tri-state descriptor,
``services/results_evidence.py``) resolves to ``NOT_CHECKABLE``, never to
``CHECKED_CLEAN``. "Queue collection was armed and returned nothing" is not
"there are no queues", and reporting the second when the first is true is
precisely the failure this project treats as worse than a refusal
(CLAUDE.md: *a refused edit with a clear reason beats a completed edit with
wrong numbers*). A partially-checkable anomaly reports ``NOT_CHECKABLE``
too — the per-check detail carries the nuance, but the headline never
rounds a gap up to "clean".

The rule runs in the other direction as well: if a constraint indexed by a
``NOT_YET_ARMED``/``NOT_MEASURABLE`` anomaly ever fires anyway, the finding
is reported (``DETECTED``, ``arming_contradicted: True``) and the
catalogue's own status claim is what gets called stale. A finding is never
suppressed to keep a status tidy, and no postrun finding can fall out of a
report: anything not surfaced under an anomaly lands in
``uncatalogued_findings``.

Coverage is derived, never hand-asserted
----------------------------------------
Same doctrine as ``constraints.get_edit_time_only_constraints()``: a
function computes the mapping from **live registry state** so a constraint
that is renamed or removed cannot leave a stale claim behind.

* :func:`get_anomaly_coverage` resolves each anomaly's declared constraint
  ids against ``constraints.get_registry()`` *at call time*. An id that no
  longer resolves (renamed, deleted) — or that resolves to a constraint
  whose phase is not ``"postrun"``, which this scan can never run — lands in
  ``unresolved_constraint_ids``, and an anomaly that declared ``ARMED``
  degrades to :data:`DETECTOR_UNREGISTERED`. It cannot keep claiming to be
  armed by a detector that no longer exists.
* :func:`get_uncatalogued_postrun_constraints` is the mirror image: every
  registered postrun constraint that **no** anomaly indexes. The catalogue
  states what it does not cover rather than implying the four names are the
  whole of the postrun catalog.

Registration deliberately does **not** raise on an unknown constraint id
(unlike ``constraints.register_dataset_check``): a constraint rename must
not crash backend import, and more to the point, a raise there would make
the stale-claim failure invisible instead of reportable. The fail-fast lives
in the test suite (``test_agent_anomalies.py`` asserts zero unresolved ids
today), which is where a rename should be caught.

Arming status per anomaly — measured, not estimated
---------------------------------------------------
* ``ghost_queue`` / ``blocked_connector`` — :data:`ARMED`. Unit U1 put the
  evidence in ``run_result`` and three postrun constraints went from
  never-firing to firing; these two anomalies index them.
* ``starved_link`` — :data:`NOT_YET_ARMED`, **not** ``NOT_MEASURABLE``. The
  evidence exists on the customer network today: 70 of 70 customer-model
  result .dbs in this checkout carry
  ``LINKEVALSEGMENT_EvaluationTimeIntervalClass``, columns
  ``ARG_TIMEINTERVAL, ARG_VEHICLECLASS, OBJECT_ID, VEHDISTTOT, VEHTMTOT,
  DELAYTOT`` (measured for this unit, ``mode=ro&immutable=1``, table names
  and ``PRAGMA table_info`` only — no row data read). Unit U1's own
  capability matrix, ``backend/tests/fixtures/result_evidence/families.json``,
  independently records the same table as ``PRESENT`` on that family at
  2243–13656 rows. What is missing is upstream plumbing — ``vissim_com``
  arms only four evaluation types and ``results_reader`` reads no LINK
  table — which is a scheduled unit, not a research gap. The distinction is
  load-bearing: ``NOT_MEASURABLE`` would tell a future reader to stop
  looking.
* ``latent_demand_diffusion`` — :data:`NOT_MEASURABLE`, the only true
  declared gap of the four. The verified reason is recorded in
  ``services/agent/tools_run.py``'s module docstring and is **attached**
  here (:func:`unobtainable_from_result_db_reason` reads it out of that
  docstring at call time) rather than restated, so the two cannot drift.

No COM, no I/O
--------------
Everything here operates on an in-memory ``run_result`` payload that some
earlier stage already produced. Nothing in this module opens a database,
touches Vissim, or writes anything.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from services import results_evidence
from services.agent import constraints

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Arming status (a property of the catalogue) — four values
# ---------------------------------------------------------------------------

#: At least one registered postrun constraint detects this anomaly today.
ARMED = "ARMED"
#: No detector yet, but the evidence exists on real networks — the missing
#: piece is scheduled plumbing, not an open research question.
NOT_YET_ARMED = "NOT_YET_ARMED"
#: Verified unobtainable from the evidence this system can read at all.
NOT_MEASURABLE = "NOT_MEASURABLE"
#: Derived, never declared: the anomaly claimed a detector that is no longer
#: in the live constraint registry (renamed, removed, or re-phased). The
#: anti-stale-claim state — a hardcoded coverage table cannot produce it.
DETECTOR_UNREGISTERED = "DETECTOR_UNREGISTERED"

ARMING_STATUSES = (ARMED, NOT_YET_ARMED, NOT_MEASURABLE, DETECTOR_UNREGISTERED)
_DECLARABLE_ARMING = (ARMED, NOT_YET_ARMED, NOT_MEASURABLE)


# ---------------------------------------------------------------------------
# Scan outcome (a property of one run) — three values
# ---------------------------------------------------------------------------

#: An indexed constraint fired on this run.
DETECTED = "DETECTED"
#: Every indexed constraint ran against real evidence and none fired.
CHECKED_CLEAN = "CHECKED_CLEAN"
#: The check could not be performed. The report says why.
NOT_CHECKABLE = "NOT_CHECKABLE"

OUTCOMES = (DETECTED, CHECKED_CLEAN, NOT_CHECKABLE)


# ---------------------------------------------------------------------------
# Why a piece of evidence was not usable
# ---------------------------------------------------------------------------

#: The evidence key carries rows — the check can run.
AVAILABLE = "AVAILABLE"
#: The run payload has no such key at all (tools_run omits a key whose
#: backing table is absent, and omits ``delay_segments`` when the .inpx
#: carries no delay-segment start to compare against).
KEY_OMITTED = "KEY_OMITTED"
#: Present but empty with no ``ARMED_BUT_EMPTY`` corroboration — the reader
#: ran and produced nothing.
EMPTY = "EMPTY"
#: There is no run payload at all.
NO_RUN_RESULT = "NO_RUN_RESULT"
#: A non-``ARMED`` anomaly's indexed constraint fired anyway — the
#: catalogue's own status claim is stale, and the finding wins.
ARMING_CONTRADICTED = "ARMING_CONTRADICTED"
# ``results_evidence.ARMED_BUT_EMPTY`` and ``results_evidence.TABLE_ABSENT``
# are the other two states and are reused verbatim rather than re-spelled —
# U1 owns those names.

#: Only this one lets a check run.
_CHECKABLE_STATES = frozenset({AVAILABLE})


# ---------------------------------------------------------------------------
# Catalogue types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexedCheck:
    """One existing constraint, indexed onto one anomaly.

    ``required_run_result_keys`` are the ``run_result`` keys the constraint's
    check function **early-returns on** — the keys whose absence makes it
    silent rather than clean. They are NOT every key it reads: e.g.
    ``queue_length_definition_not_portable`` reads ``queue_definition`` too,
    but that key's *absence* is the finding, so requiring it would report a
    genuine hit as un-checkable.

    ``evidence_tables`` are the result-.db tables those keys are read from,
    used to explain an omission through unit U1's evidence descriptor
    (``TABLE_ABSENT`` vs ``ARMED_BUT_EMPTY``).
    """

    constraint_id: str
    required_run_result_keys: tuple[str, ...]
    evidence_tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class Anomaly:
    """One roadmap-named anomaly and the constraints that speak to it."""

    id: str
    label_ko: str
    label_en: str
    label_ja: str
    arming: str
    reason_ko: str
    reason_en: str
    reason_ja: str
    checks: tuple[IndexedCheck, ...] = ()
    #: Where the measured reason behind ``arming`` was established, when it
    #: was established somewhere else. Attached, not restated.
    reason_source: str | None = None
    #: The verbatim measured reason from ``reason_source``, when one can be
    #: read out of it at call time.
    reason_verbatim: str | None = None
    roadmap_rows: tuple[str, ...] = ("2.1", "3.7")

    @property
    def constraint_ids(self) -> tuple[str, ...]:
        return tuple(check.constraint_id for check in self.checks)

    @property
    def evidence_tables(self) -> tuple[str, ...]:
        seen: list[str] = []
        for check in self.checks:
            for table in check.evidence_tables:
                if table not in seen:
                    seen.append(table)
        return tuple(seen)

    @property
    def run_result_keys(self) -> tuple[str, ...]:
        seen: list[str] = []
        for check in self.checks:
            for key in check.required_run_result_keys:
                if key not in seen:
                    seen.append(key)
        return tuple(seen)

    def label(self) -> dict[str, str]:
        return {"ko": self.label_ko, "en": self.label_en, "ja": self.label_ja}

    def reason(self) -> dict[str, str]:
        return {"ko": self.reason_ko, "en": self.reason_en, "ja": self.reason_ja}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Anomaly] = {}


def register_anomaly(anomaly: Anomaly) -> Anomaly:
    """Add an anomaly to the module-level catalogue, keyed by its id.

    Validates only what is knowable without the constraint registry — a
    duplicate id, an ``ARMED`` declaration with no indexed check, an
    undeclarable arming value. It deliberately does **not** raise on an
    unknown constraint id (contrast ``constraints.register_dataset_check``,
    which does): whether an indexed id still resolves is exactly the thing
    :func:`get_anomaly_coverage` derives live, and raising here would turn a
    reportable stale claim into an import-time crash of the whole backend.
    """
    if anomaly.id in _REGISTRY:
        raise ValueError(f"register_anomaly: duplicate anomaly id {anomaly.id!r}")
    if anomaly.arming not in _DECLARABLE_ARMING:
        raise ValueError(
            f"register_anomaly: {anomaly.id!r} declared arming {anomaly.arming!r}; "
            f"declarable values are {list(_DECLARABLE_ARMING)} "
            f"({DETECTOR_UNREGISTERED} is derived, never declared)"
        )
    if anomaly.arming == ARMED and not anomaly.checks:
        raise ValueError(
            f"register_anomaly: {anomaly.id!r} declares {ARMED} but indexes no constraint"
        )
    if not (anomaly.reason_ko and anomaly.reason_en and anomaly.reason_ja):
        raise ValueError(f"register_anomaly: {anomaly.id!r} needs a ko/en/ja reason")
    _REGISTRY[anomaly.id] = anomaly
    return anomaly


def get_anomaly_registry() -> tuple[Anomaly, ...]:
    """Every registered anomaly, in registration order."""
    return tuple(_REGISTRY.values())


def get_anomaly(anomaly_id: str) -> Anomaly | None:
    return _REGISTRY.get(anomaly_id)


# ---------------------------------------------------------------------------
# Derived coverage — computed from live registry state on every call
# ---------------------------------------------------------------------------


def _resolve(anomaly: Anomaly) -> tuple[tuple[IndexedCheck, ...], tuple[dict[str, str], ...]]:
    """Split an anomaly's indexed checks into (resolved, unresolved).

    An id is unresolved when it is not in the live constraint registry at
    all, or when it is registered under a phase this scan never runs — a
    postrun scan cannot fire a preflight check, so indexing one would be a
    coverage claim with nothing behind it.
    """
    by_id = {c.id: c for c in constraints.get_registry()}
    resolved: list[IndexedCheck] = []
    unresolved: list[dict[str, str]] = []
    for check in anomaly.checks:
        constraint = by_id.get(check.constraint_id)
        if constraint is None:
            unresolved.append({"constraint_id": check.constraint_id, "why": "NOT_REGISTERED"})
        elif constraint.phase != "postrun":
            unresolved.append(
                {
                    "constraint_id": check.constraint_id,
                    "why": "WRONG_PHASE",
                    "phase": constraint.phase,
                }
            )
        else:
            resolved.append(check)
    return tuple(resolved), tuple(unresolved)


def effective_arming(anomaly: Anomaly) -> tuple[str, tuple[IndexedCheck, ...], tuple[dict[str, str], ...]]:
    """``(arming, resolved_checks, unresolved)`` from LIVE registry state.

    A declared-``ARMED`` anomaly whose indexed detector no longer resolves
    degrades to :data:`DETECTOR_UNREGISTERED`. This is the whole reason the
    coverage is derived rather than tabulated: a rename in constraints.py
    changes this return value, and no hand-written table can.
    """
    resolved, unresolved = _resolve(anomaly)
    if anomaly.arming == ARMED and (unresolved or not resolved):
        return DETECTOR_UNREGISTERED, resolved, unresolved
    return anomaly.arming, resolved, unresolved


def get_anomaly_coverage() -> dict[str, dict[str, Any]]:
    """``{anomaly_id: coverage}`` derived from live registry state.

    Mirrors ``constraints.get_edit_time_only_constraints()``'s contract: the
    coverage claim is computed, so it cannot outlive the constraints it
    claims. Every field below is a derivation, not a stored value.
    """
    coverage: dict[str, dict[str, Any]] = {}
    for anomaly in _REGISTRY.values():
        arming, resolved, unresolved = effective_arming(anomaly)
        coverage[anomaly.id] = {
            "id": anomaly.id,
            "label": anomaly.label(),
            "declared_arming": anomaly.arming,
            "arming": arming,
            "reason": anomaly.reason(),
            "reason_source": anomaly.reason_source,
            "reason_verbatim": anomaly.reason_verbatim,
            "declared_constraint_ids": list(anomaly.constraint_ids),
            "constraint_ids": [check.constraint_id for check in resolved],
            "unresolved_constraint_ids": [dict(entry) for entry in unresolved],
            "evidence_tables": list(anomaly.evidence_tables),
            "run_result_keys": list(anomaly.run_result_keys),
            "roadmap_rows": list(anomaly.roadmap_rows),
        }
    return coverage


def get_catalogued_constraint_ids() -> frozenset[str]:
    """Every constraint id currently reachable through this catalogue —
    resolved against the live registry, so a vanished id is not counted."""
    ids: set[str] = set()
    for anomaly in _REGISTRY.values():
        resolved, _ = _resolve(anomaly)
        ids.update(check.constraint_id for check in resolved)
    return frozenset(ids)


def get_uncatalogued_postrun_constraints() -> tuple[constraints.Constraint, ...]:
    """Every registered **postrun** constraint that no anomaly indexes.

    The mirror of ``constraints.get_edit_time_only_constraints()``, and the
    catalogue's honesty about its own edges: roadmap row 2.1 names four
    anomalies, the postrun catalog has more constraints than that, and a
    scan that quietly dropped the rest would imply a coverage it does not
    have. :func:`scan_run_result` surfaces any finding from these under
    ``uncatalogued_findings`` rather than discarding it.
    """
    catalogued = get_catalogued_constraint_ids()
    return tuple(
        c for c in constraints.get_registry() if c.phase == "postrun" and c.id not in catalogued
    )


# ---------------------------------------------------------------------------
# The measured reason for the one true gap — attached, not restated
# ---------------------------------------------------------------------------

_TOOLS_RUN_MODULE = "services/agent/tools_run.py"
_UNOBTAINABLE_ANCHOR = "deleted_vehicles is always None today"
_UNOBTAINABLE_FALLBACK = (
    f"see {_TOOLS_RUN_MODULE}'s module docstring, paragraph "
    f"'{_UNOBTAINABLE_ANCHOR}', for the verified reason"
)


def unobtainable_from_result_db_reason() -> str:
    """The verified reason latent demand / diffusion is unobtainable, read
    out of ``tools_run``'s own module docstring at call time.

    Attached rather than restated on purpose: unit U1 established this fact
    and owns the wording, and a copy here could drift away from it silently.
    If the anchor paragraph is ever renamed the fallback names the file and
    the paragraph so the trail is still followable (and
    ``test_agent_anomalies.py`` fails, which is where that should surface).
    """
    try:
        from services.agent import tools_run
    except Exception:  # pragma: no cover - import failure is not this unit's story
        logger.exception("anomalies: could not import tools_run for its measured reason")
        return _UNOBTAINABLE_FALLBACK

    doc = tools_run.__doc__ or ""
    for paragraph in doc.split("\n\n"):
        if _UNOBTAINABLE_ANCHOR in paragraph:
            return re.sub(r"\s+", " ", paragraph).strip()
    return _UNOBTAINABLE_FALLBACK


def armed_evaluation_types() -> tuple[str, ...]:
    """The evaluation types ``vissim_com`` actually arms, read live.

    Derived so ``starved_link``'s ``NOT_YET_ARMED`` status names the real
    gap instead of a remembered one: the day a link-evaluation type is added
    there, this tuple grows and the catalogue's own test notices.
    """
    try:
        from services import vissim_com
    except Exception:  # pragma: no cover - import failure is not this unit's story
        logger.exception("anomalies: could not import vissim_com for its armed evaluation types")
        return ()
    return tuple(sorted(getattr(vissim_com, "_EVAL_TYPE_ATTR_IDS", {})))


# ---------------------------------------------------------------------------
# The catalogue itself
# ---------------------------------------------------------------------------

_MOVEMENT_INTERVAL = results_evidence.MOVEMENT_INTERVAL_TABLE
_MOVEMENT_CLASS = results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE
_LINK_SEGMENT = "LINKEVALSEGMENT_EvaluationTimeIntervalClass"


def _register_catalogue() -> None:
    """Idempotent (guarded by ``_registered``): registering twice would
    raise on the duplicate id. Called at import time, mirroring
    ``health_check.py``'s ``_ensure_registered`` pattern."""

    # -- 유령 대기열 / ghost queue ------------------------------------------
    # ARMED by unit U1. Neither indexed constraint is a "this queue is not
    # real" oracle -- no such oracle exists in Vissim's output -- but both
    # are the run-visible signature of a queue NUMBER that does not mean
    # what it appears to mean, which is what the anomaly names:
    #   * queue_length_definition_not_portable -- queue values reported
    #     without the speed thresholds/counter placement that define them;
    #     the same number describes different phenomena in two models.
    #   * delay_measurement_truncated_by_queue -- the queue outruns the
    #     delay-measurement segment, so the delay attributed to it is a
    #     fraction of the real one.
    register_anomaly(
        Anomaly(
            id="ghost_queue",
            label_ko="유령 대기열",
            label_en="Ghost queue",
            label_ja="幽霊待ち行列",
            arming=ARMED,
            reason_ko="유닛 U1이 MOVEMENT_EvaluationTimeInterval(QLEN/QLENMAX)의 대기행렬 "
            "증거를 run_result에 실으면서 두 개의 postrun 제약이 처음으로 실제 발화하게 "
            "되었습니다.",
            reason_en="Unit U1 put the queue evidence (QLEN/QLENMAX from "
            "MOVEMENT_EvaluationTimeInterval) into run_result, which made the two indexed "
            "postrun constraints fire for the first time.",
            reason_ja="ユニットU1がMOVEMENT_EvaluationTimeInterval(QLEN/QLENMAX)の待ち行列"
            "エビデンスをrun_resultに載せたことで、2つのpostrun制約が初めて実際に発火する"
            "ようになりました。",
            checks=(
                IndexedCheck(
                    constraint_id="queue_length_definition_not_portable",
                    required_run_result_keys=("queue_results",),
                    evidence_tables=(_MOVEMENT_INTERVAL,),
                ),
                IndexedCheck(
                    constraint_id="delay_measurement_truncated_by_queue",
                    required_run_result_keys=("delay_segments",),
                    evidence_tables=(_MOVEMENT_INTERVAL,),
                ),
            ),
        )
    )

    # -- 차단 커넥터 / blocked connector -------------------------------------
    # ARMED by unit U1. gridlock_reports_los_a is the run-visible signature:
    # a movement graded LOS A (the BEST grade) on zero throughput. Vissim's
    # LOS A covers both "no delay" and "nothing moved at all", and nothing
    # moving through a movement is what a blocked connector looks like from
    # the result .db. Named a proxy here rather than sold as a direct
    # connector-state oracle: the result database carries no connector
    # status column to read.
    register_anomaly(
        Anomaly(
            id="blocked_connector",
            label_ko="차단된 커넥터",
            label_en="Blocked connector",
            label_ja="遮断されたコネクタ",
            arming=ARMED,
            reason_ko="유닛 U1이 MOVEMENT_EvaluationTimeIntervalClass(VEHS/VEHDELAY)에서 "
            "run_result['los_by_movement']를 만들면서, 통행량 0인데 LOS A로 보고되는 "
            "움직임을 실제로 잡아낼 수 있게 되었습니다. 결과 DB에는 커넥터 상태 자체를 "
            "읽을 수 있는 항목이 없으므로 이는 직접 관측이 아니라 대리 지표입니다.",
            reason_en="Unit U1 built run_result['los_by_movement'] from "
            "MOVEMENT_EvaluationTimeIntervalClass (VEHS/VEHDELAY), so a movement reported at "
            "LOS A on zero throughput is now genuinely catchable. This is a proxy, not a "
            "direct observation: the result database carries no connector-state column.",
            reason_ja="ユニットU1がMOVEMENT_EvaluationTimeIntervalClass(VEHS/VEHDELAY)から"
            "run_result['los_by_movement']を構築したため、通行量0でLOS Aと報告される移動を"
            "実際に検出できるようになりました。結果DBにコネクタ状態の列は無いため、これは"
            "直接観測ではなく代理指標です。",
            checks=(
                IndexedCheck(
                    constraint_id="gridlock_reports_los_a",
                    required_run_result_keys=("los_by_movement",),
                    evidence_tables=(_MOVEMENT_CLASS,),
                ),
            ),
        )
    )

    # -- 기아 링크 / starved link --------------------------------------------
    # NOT_YET_ARMED, deliberately NOT NOT_MEASURABLE. See the module
    # docstring: the evidence is present on 70/70 customer-model result .dbs
    # in this checkout; the missing piece is plumbing on the arming and
    # reading sides, which is a scheduled unit.
    armed_types = ", ".join(armed_evaluation_types()) or "(none readable)"
    register_anomaly(
        Anomaly(
            id="starved_link",
            label_ko="기아 링크",
            label_en="Starved link",
            label_ja="飢餓リンク",
            arming=NOT_YET_ARMED,
            reason_ko=f"증거는 이미 존재합니다 — 이 체크아웃의 고객 모델 결과 .db 70개 "
            f"전부가 {_LINK_SEGMENT} 테이블을 세그먼트별 VEHDISTTOT/VEHTMTOT/DELAYTOT과 "
            f"함께 가지고 있습니다(읽기 전용으로 스키마만 실측). 빠진 것은 배관입니다: "
            f"vissim_com은 평가 유형을 {armed_types} 네 가지만 무장하고, results_reader는 "
            f"어떤 LINK 테이블도 읽지 않습니다. 연구 공백이 아니라 예정된 작업 단위입니다.",
            reason_en=f"The evidence already exists — all 70 customer-model result .dbs in "
            f"this checkout carry {_LINK_SEGMENT} with per-segment VEHDISTTOT/VEHTMTOT/"
            f"DELAYTOT (measured read-only, schema only). What is missing is plumbing: "
            f"vissim_com arms only the four evaluation types {armed_types}, and "
            f"results_reader reads no LINK table at all. A scheduled unit, not a research gap.",
            reason_ja=f"エビデンスは既に存在します — このチェックアウトの顧客モデル結果.db "
            f"70件すべてが{_LINK_SEGMENT}をセグメント別のVEHDISTTOT/VEHTMTOT/DELAYTOTと"
            f"共に保持しています(読み取り専用・スキーマのみ実測)。欠けているのは配管です: "
            f"vissim_comは評価タイプを{armed_types}の4種類しか武装せず、results_readerは"
            f"LINKテーブルを一切読みません。研究上の空白ではなく、予定された作業単位です。",
            reason_source=(
                "measured read-only over the customer-model result .dbs "
                "(mode=ro&immutable=1, schema only); cross-checked against "
                "backend/tests/fixtures/result_evidence/families.json"
            ),
            checks=(),
        )
    )

    # -- diffusion / latent demand > 1% --------------------------------------
    # NOT_MEASURABLE — the only true declared gap of the four. Both indexed
    # constraints ARE registered and would fire if their run_result key ever
    # appeared; neither key is obtainable from the result .db schema, and
    # tools_run.py records why. The reason is attached from that docstring,
    # not retyped here.
    register_anomaly(
        Anomaly(
            id="latent_demand_diffusion",
            label_ko="확산/잠재 수요 (>1%)",
            label_en="Diffusion / latent demand (>1%)",
            label_ja="拡散/潜在需要 (>1%)",
            arming=NOT_MEASURABLE,
            reason_ko="결과 .db 스키마에서 얻을 수 없습니다. 검증된 사유는 "
            f"{_TOOLS_RUN_MODULE}의 모듈 docstring에 기록되어 있으며 여기에 그대로 "
            "첨부됩니다(재서술하지 않음): Vissim은 DiffusTm에 의한 차량 제거를 실행의 "
            ".err 텍스트 로그에만 기록하고 어떤 SQLite 결과 테이블에도 남기지 않으며, "
            "latent_demand_vehicles도 마찬가지입니다.",
            reason_en="Not obtainable from the result .db schema. The verified reason is "
            f"recorded in {_TOOLS_RUN_MODULE}'s module docstring and attached verbatim "
            "below rather than restated.",
            reason_ja="結果.dbのスキーマからは取得できません。検証済みの理由は"
            f"{_TOOLS_RUN_MODULE}のモジュールdocstringに記録されており、ここでは再記述せず"
            "そのまま添付します: VissimはDiffusTmによる車両削除を実行の.errテキストログ"
            "にのみ記録し、SQLite結果テーブルには一切残しません。latent_demand_vehicles"
            "も同様です。",
            reason_source=f"{_TOOLS_RUN_MODULE} module docstring, paragraph '{_UNOBTAINABLE_ANCHOR}'",
            reason_verbatim=unobtainable_from_result_db_reason(),
            checks=(
                IndexedCheck(
                    constraint_id="latent_demand_residue_after_run",
                    required_run_result_keys=("latent_demand_vehicles",),
                    evidence_tables=(),
                ),
                IndexedCheck(
                    constraint_id="vehicles_deleted_in_run",
                    required_run_result_keys=("deleted_vehicles",),
                    evidence_tables=(),
                ),
            ),
        )
    )


_registered = False


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        _register_catalogue()
        _registered = True


_ensure_registered()


# ---------------------------------------------------------------------------
# Why-not-checkable messages (ko primary, en/ja alongside)
# ---------------------------------------------------------------------------

_WHY_MESSAGES: dict[str, dict[str, str]] = {
    results_evidence.ARMED_BUT_EMPTY: {
        "ko": "'{key}'({tables}) 수집이 요청되어 테이블은 만들어졌지만 0행이 돌아왔습니다 — "
        "데이터가 없다는 사실이 측정된 것이지, 이상이 없다는 뜻이 아닙니다. 확인 불가로 "
        "보고합니다.",
        "en": "'{key}' ({tables}) was armed and the table was created, but the run collected "
        "zero rows — that is measured absence of DATA, not absence of an anomaly. Reported as "
        "not checkable.",
        "ja": "'{key}'({tables})は収集が要求されテーブルは作成されましたが、0行が返りました — "
        "これはデータが無いという測定結果であり、異常が無いという意味ではありません。確認"
        "不可として報告します。",
    },
    results_evidence.TABLE_ABSENT: {
        "ko": "'{key}'의 근거 테이블({tables})이 결과 DB에 아예 없습니다 — 해당 평가가 "
        "무장되지 않았습니다. 이 실행은 이 항목에 대해 아무 의견도 갖고 있지 않습니다.",
        "en": "The table backing '{key}' ({tables}) is not in the result database at all — "
        "that evaluation was never armed. This run holds no opinion on this anomaly.",
        "ja": "'{key}'の根拠テーブル({tables})が結果DBに存在しません — その評価は武装されて"
        "いません。この実行はこの項目について何の見解も持ちません。",
    },
    KEY_OMITTED: {
        "ko": "실행 결과에 '{key}' 항목이 없습니다 — 이 실행은 해당 증거를 싣지 않았습니다.",
        "en": "The run payload carries no '{key}' key — this run shipped no such evidence.",
        "ja": "実行結果に'{key}'キーがありません — この実行はそのエビデンスを含んでいません。",
    },
    EMPTY: {
        "ko": "'{key}'는 존재하지만 비어 있습니다 — 판단할 근거가 없습니다.",
        "en": "'{key}' is present but empty — there is nothing to judge.",
        "ja": "'{key}'は存在しますが空です — 判断する根拠がありません。",
    },
    NO_RUN_RESULT: {
        "ko": "실행 결과 자체가 없습니다 — 어떤 postrun 점검도 수행할 수 없습니다.",
        "en": "There is no run payload at all — no postrun check can be performed.",
        "ja": "実行結果自体がありません — いかなるpostrunチェックも実行できません。",
    },
    NOT_YET_ARMED: {
        "ko": "이 이상 징후를 감지하는 제약이 아직 없습니다(예정된 작업). 확인 불가.",
        "en": "No constraint detects this anomaly yet (scheduled work). Not checkable.",
        "ja": "この異常を検出する制約はまだありません(予定作業)。確認不可。",
    },
    NOT_MEASURABLE: {
        "ko": "이 시스템이 읽을 수 있는 증거로는 관측 자체가 불가능합니다. 확인 불가.",
        "en": "Not observable at all from the evidence this system can read. Not checkable.",
        "ja": "このシステムが読み取れるエビデンスでは観測自体が不可能です。確認不可。",
    },
    DETECTOR_UNREGISTERED: {
        "ko": "이 이상 징후가 지목한 제약({ids})이 현재 제약 레지스트리에 없습니다 — "
        "카탈로그의 커버리지 주장이 실제 제약과 어긋났습니다. 확인 불가.",
        "en": "The constraint(s) this anomaly indexes ({ids}) are not in the live constraint "
        "registry — the catalogue's coverage claim has drifted from the constraints. Not "
        "checkable.",
        "ja": "この異常が参照する制約({ids})が現在の制約レジストリにありません — カタログの"
        "カバレッジ主張が実際の制約と乖離しています。確認不可。",
    },
    ARMING_CONTRADICTED: {
        "ko": "이 이상 징후는 카탈로그상 '{arming}' 상태이지만, 지목한 제약({ids})이 이번 "
        "실행에서 실제로 발화했습니다 — 카탈로그의 상태 주장이 낡았습니다. 발화한 내용을 "
        "그대로 보고합니다.",
        "en": "This anomaly is catalogued as '{arming}', but the constraint(s) it indexes "
        "({ids}) actually fired on this run — the catalogue's status claim is stale. The "
        "findings are reported as they came.",
        "ja": "この異常はカタログ上「{arming}」ですが、参照する制約({ids})が今回の実行で"
        "実際に発火しました — カタログの状態主張が古くなっています。発火した内容をその"
        "まま報告します。",
    },
}


def _why(state: str, **kwargs: Any) -> dict[str, str]:
    template = _WHY_MESSAGES.get(state)
    if template is None:  # pragma: no cover - every state above is covered
        return {lang: f"{state}" for lang in ("ko", "en", "ja")}
    return {lang: text.format(**kwargs) for lang, text in template.items()}


# ---------------------------------------------------------------------------
# Evidence availability
# ---------------------------------------------------------------------------

# Same two-axis bound health_check.py applies to its own report: a details
# payload whose size tracks NETWORK size has flooded a message in this
# codebase once already (see constraints.py's _capped_join docstring), and a
# scan report is exactly the kind of artifact that gets rendered straight
# into a UI list.
_MAX_DETAIL_ITEMS = 20


def _descriptor(run_result: Mapping[str, Any] | None) -> dict[str, str]:
    if not run_result:
        return {}
    evidence = run_result.get("evidence")
    return dict(evidence) if isinstance(evidence, Mapping) else {}


def _evidence_state(
    run_result: Mapping[str, Any],
    key: str,
    tables: Sequence[str],
    descriptor: Mapping[str, str],
) -> dict[str, Any]:
    """The state of one required ``run_result`` key, with its reason.

    Order matters. The in-process ``EmptyEvidence*`` marker is consulted
    first because it carries its own reason and source; the descriptor is
    the fallback, and is what survives a JSON round trip (the marker classes
    serialise to a bare ``{}``/``[]`` — U1's own docstring says so, which is
    exactly why ``run_result["evidence"][table] == "ARMED_BUT_EMPTY"`` is
    the durable form and must be honoured here too).
    """
    table_text = ", ".join(tables) or "-"
    table_states = [descriptor.get(table) for table in tables]

    def _entry(state: str, *, marker: Any = None) -> dict[str, Any]:
        return {
            "key": key,
            "state": state,
            "tables": list(tables),
            "why": None if state == AVAILABLE else _why(state, key=key, tables=table_text),
            # The marker's own words when there is a marker, kept alongside
            # the catalogue's. Always present so consumers get one shape.
            "marker_reason": getattr(marker, "reason", None),
            "marker_source": getattr(marker, "source", None),
        }

    if key not in run_result:
        if results_evidence.ARMED_BUT_EMPTY in table_states:
            return _entry(results_evidence.ARMED_BUT_EMPTY)
        if table_states and all(s == results_evidence.TABLE_ABSENT for s in table_states):
            return _entry(results_evidence.TABLE_ABSENT)
        return _entry(KEY_OMITTED)

    value = run_result[key]
    if isinstance(value, (results_evidence.EmptyEvidenceMapping, results_evidence.EmptyEvidenceList)):
        return _entry(results_evidence.ARMED_BUT_EMPTY, marker=value)

    if value:
        return _entry(AVAILABLE)

    # Present but falsy and no marker class — either a genuine zero-row read
    # or a marker that lost its type crossing a JSON boundary. The descriptor
    # is what tells those apart.
    if results_evidence.ARMED_BUT_EMPTY in table_states:
        return _entry(results_evidence.ARMED_BUT_EMPTY)
    return _entry(EMPTY)


def _bound(items: Sequence[Any]) -> Any:
    if len(items) <= _MAX_DETAIL_ITEMS:
        return list(items)
    return list(items[:_MAX_DETAIL_ITEMS]) + [f"... +{len(items) - _MAX_DETAIL_ITEMS} more"]


def _finding_to_dict(finding: constraints.Finding) -> dict[str, Any]:
    details = {
        k: _bound(v) if isinstance(v, (list, tuple)) else v
        for k, v in (finding.details or {}).items()
    }
    return {
        "constraint_id": finding.constraint_id,
        "tier": finding.tier,
        "phase": finding.phase,
        "messages": dict(finding.messages),
        "details": details,
    }


# ---------------------------------------------------------------------------
# The scan entry point — two callers
# ---------------------------------------------------------------------------


def index_postrun_findings(
    findings: Iterable[constraints.Finding],
    run_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Index already-computed postrun findings onto the catalogue.

    The entry point for roadmap row 2.1's calibration loop, which has a
    ``GuardrailReport`` in hand from ``GuardrailEngine.postrun`` and must not
    pay to run the same checks twice. ``run_result`` is still required — the
    findings alone say what fired, and this unit's whole job is to also say
    what could not be checked, which only the evidence answers.
    """
    findings = list(findings)
    by_constraint: dict[str, list[constraints.Finding]] = {}
    for finding in findings:
        by_constraint.setdefault(finding.constraint_id, []).append(finding)

    descriptor = _descriptor(run_result)
    payload: Mapping[str, Any] = run_result if isinstance(run_result, Mapping) else {}

    reported: list[dict[str, Any]] = []
    summary = {DETECTED: 0, CHECKED_CLEAN: 0, NOT_CHECKABLE: 0}
    detected_ids: list[str] = []
    not_checkable_ids: list[str] = []
    clean_ids: list[str] = []

    for anomaly in _REGISTRY.values():
        arming, resolved, unresolved = effective_arming(anomaly)
        entry: dict[str, Any] = {
            "id": anomaly.id,
            "label": anomaly.label(),
            "arming": arming,
            "declared_arming": anomaly.arming,
            "reason": anomaly.reason(),
            "reason_source": anomaly.reason_source,
            "reason_verbatim": anomaly.reason_verbatim,
            "constraint_ids": [check.constraint_id for check in resolved],
            "unresolved_constraint_ids": [dict(u) for u in unresolved],
            "evidence_tables": list(anomaly.evidence_tables),
            "roadmap_rows": list(anomaly.roadmap_rows),
            "checks": [],
            "findings": [],
            # Set below when a non-ARMED anomaly's detector fires anyway.
            # Always present so consumers get one shape, never two.
            "arming_contradicted": False,
        }

        if arming != ARMED:
            # A non-ARMED anomaly still gets its resolved constraints' hits
            # attached. If one fires, the CATALOGUE is what was wrong, not
            # the run — and swallowing the finding to protect a status claim
            # is precisely the failure this unit exists to prevent.
            fired = [f for check in resolved for f in by_constraint.get(check.constraint_id, [])]
            entry["findings"] = [_finding_to_dict(f) for f in fired]
            if fired:
                entry["outcome"] = DETECTED
                entry["arming_contradicted"] = True
                entry["why"] = _why(
                    ARMING_CONTRADICTED,
                    arming=arming,
                    ids=", ".join(sorted({f.constraint_id for f in fired})),
                )
                summary[DETECTED] += 1
                detected_ids.append(anomaly.id)
            else:
                entry["outcome"] = NOT_CHECKABLE
                entry["why"] = _why(
                    arming,
                    ids=", ".join(u["constraint_id"] for u in unresolved) or "-",
                )
                summary[NOT_CHECKABLE] += 1
                not_checkable_ids.append(anomaly.id)
            reported.append(entry)
            continue

        if run_result is None:
            entry["outcome"] = NOT_CHECKABLE
            entry["why"] = _why(NO_RUN_RESULT)
            reported.append(entry)
            summary[NOT_CHECKABLE] += 1
            not_checkable_ids.append(anomaly.id)
            continue

        all_checkable = True
        for check in resolved:
            evidence = [
                _evidence_state(payload, key, check.evidence_tables, descriptor)
                for key in check.required_run_result_keys
            ]
            checkable = bool(evidence) and all(e["state"] in _CHECKABLE_STATES for e in evidence)
            all_checkable = all_checkable and checkable
            hits = by_constraint.get(check.constraint_id, [])
            entry["checks"].append(
                {
                    "constraint_id": check.constraint_id,
                    "checkable": checkable,
                    "fired": bool(hits),
                    "evidence": evidence,
                }
            )
            entry["findings"].extend(_finding_to_dict(f) for f in hits)

        if entry["findings"]:
            entry["outcome"] = DETECTED
            entry["why"] = None
            summary[DETECTED] += 1
            detected_ids.append(anomaly.id)
        elif all_checkable:
            entry["outcome"] = CHECKED_CLEAN
            entry["why"] = None
            summary[CHECKED_CLEAN] += 1
            clean_ids.append(anomaly.id)
        else:
            # Deliberately conservative: an anomaly whose checks were only
            # PARTLY performable reports NOT_CHECKABLE, never CHECKED_CLEAN.
            # The per-check detail above carries which half ran.
            entry["outcome"] = NOT_CHECKABLE
            entry["why"] = next(
                (
                    e["why"]
                    for c in entry["checks"]
                    if not c["checkable"]
                    for e in c["evidence"]
                    if e["why"] is not None
                ),
                _why(EMPTY, key=", ".join(anomaly.run_result_keys) or "-", tables="-"),
            )
            summary[NOT_CHECKABLE] += 1
            not_checkable_ids.append(anomaly.id)

        reported.append(entry)

    # Derived from what actually got ATTACHED above, not from the catalogue's
    # id set: an anomaly that reports NOT_CHECKABLE attaches no findings, and
    # keying this off "is the id catalogued" would let such a finding fall
    # between the two lists and disappear. Nothing may disappear.
    attached = {
        finding["constraint_id"] for entry in reported for finding in entry["findings"]
    }
    uncatalogued = [_finding_to_dict(f) for f in findings if f.constraint_id not in attached]

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "roadmap_rows": ["2.1", "3.7"],
        "anomalies": reported,
        "summary": {
            "detected_n": summary[DETECTED],
            "checked_clean_n": summary[CHECKED_CLEAN],
            "not_checkable_n": summary[NOT_CHECKABLE],
            "detected_ids": detected_ids,
            "checked_clean_ids": clean_ids,
            "not_checkable_ids": not_checkable_ids,
        },
        # The catalogue's own edges, both derived. The id list is every
        # registered postrun constraint no anomaly indexes; the finding list
        # is every finding this report did not surface under an anomaly —
        # a superset of the first, on purpose, so nothing can fall between
        # the two and disappear.
        "uncatalogued_postrun_constraint_ids": [
            c.id for c in get_uncatalogued_postrun_constraints()
        ],
        "uncatalogued_findings": uncatalogued,
    }


def scan_run_result(
    run_result: Mapping[str, Any] | None,
    *,
    action: Mapping[str, Any] | None = None,
    shadow_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the postrun catalog over ``run_result`` and index the result.

    The entry point for roadmap row 3.7 (능동 진단): it is handed a finished
    run and nothing else — no agent turn, no proposed action, no shadow
    model — and must be able to speak up unprompted. ``action`` defaults to
    ``{"tool": "run_simulation"}`` so tool-scoped postrun constraints apply
    exactly as they would after a real run.

    Runs the same ``constraints.run_checks("postrun", ...)`` call
    ``GuardrailEngine.postrun`` makes, so no check behaviour is duplicated or
    forked here. When the caller ALREADY has that report, call
    :func:`index_postrun_findings` with its findings instead of paying for a
    second pass.
    """
    findings = constraints.run_checks(
        "postrun",
        dict(action) if action is not None else {"tool": "run_simulation"},
        dict(shadow_model) if shadow_model is not None else {},
        {"run_result": dict(run_result) if run_result is not None else None},
    )
    return index_postrun_findings(findings, run_result)


__all__ = [
    "ARMED",
    "ARMING_STATUSES",
    "AVAILABLE",
    "Anomaly",
    "CHECKED_CLEAN",
    "DETECTED",
    "DETECTOR_UNREGISTERED",
    "EMPTY",
    "IndexedCheck",
    "KEY_OMITTED",
    "NOT_CHECKABLE",
    "NOT_MEASURABLE",
    "NOT_YET_ARMED",
    "NO_RUN_RESULT",
    "ARMING_CONTRADICTED",
    "OUTCOMES",
    "armed_evaluation_types",
    "effective_arming",
    "get_anomaly",
    "get_anomaly_coverage",
    "get_anomaly_registry",
    "get_catalogued_constraint_ids",
    "get_uncatalogued_postrun_constraints",
    "index_postrun_findings",
    "register_anomaly",
    "scan_run_result",
    "unobtainable_from_result_db_reason",
]

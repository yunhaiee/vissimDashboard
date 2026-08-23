"""Unit #51 (live runner) — the JOB that runs a bounded parameter search.

What was missing, and what this is
----------------------------------
#51 built the engine, the precondition gate and the ask-first card. #51-b built
the broker that lets a finished search authorise one write. #51-c carried a
search from a proposal to a human's click through the real endpoints. Every one
of them ended at the same sentence, written three times in three modules: *no
live runner ships in this build, so nothing inside a product turn ever runs a
search*. This module and ``search_runner`` are that runner, and this one is the
half that decides WHEN a search may run and WHAT HAPPENS when it finishes.

THE ORDER, and why every step is where it is
--------------------------------------------
1. **The gate is re-derived from the server's own evidence** (:func:`evaluate_start`).
   The request body carries selectors — which scored run, which observation set,
   which behaviour, how long — and NO verdict of any kind. ``ok``, ``refusals``,
   ``exhaustion`` and ``split`` are computed here from artifacts this process
   read: a client that sent ``{"preconditions": {"ok": true}}`` would be sending
   a field nothing reads. This is not caution for its own sake; it is the same
   rule ``evaluate_preconditions`` states about itself ("there is no override
   flag, no ``force``, and no caller-supplied boolean anywhere in this
   function"), applied one layer out.
2. **The refusal is SYNCHRONOUS.** A precondition that is not met comes back on
   the POST with its named codes, not as a job that starts and dies. The named
   refusals are the product; making a user poll for one would bury it.
3. **The job runs in a thread, in this process**, exactly like the simulation
   jobs (``routers/simulations.start_agent_simulation_job``) and for the same
   reason: COM work goes through the job queue, never a request handler
   (CLAUDE.md). The workspace is captured on the SUBMITTING thread and re-bound
   on the runner thread, because ContextVars do not cross threads.
4. **Candidates are strictly sequential.** One ``VissimController`` at a time —
   ``ParameterSearch`` calls the runner synchronously, and two Vissim instances
   driven from one process is not a configuration this codebase has verified.
5. **A COMPLETE search is REMEMBERED, on the job thread**, with the session
   pointer published in a ``try``/``finally`` exactly as ``routers/assistant.py``
   does for both brokers. That is the whole point of the unit: the search has to
   land in the session the user's next turn reads, or it was an hour of Vissim
   for nothing.

WHAT A PARTIAL SEARCH DOES **NOT** DO
-------------------------------------
It does not reach the broker. ``searched_behaviour_broker`` mints a grant by
calling ``grant_from_search`` on the remembered payloads, and that function
requires a COMPLETE status — but this module does not lean on that: it never
calls ``remember`` at all unless the search finished and the winner survived its
holdout. The state dict IS written to the job's work dir as
``state.PARTIAL.NOT-MINTABLE.json`` for audit, and the name is the documentation:
the broker's own docstring (L49-58) says a resumed state must be re-run and
re-remembered IN THIS PROCESS before it counts, and nothing here reads that file
back. **Resume-from-disk is a named non-feature**, not an oversight
(:data:`RESUME_FROM_DISK`).

SEEDS
-----
Two constants, both fixed and both stated on the job record: the search runs
every calibration candidate at :data:`SEARCH_SEED`, and ``accept_winner``
re-runs the winner and the baseline at :data:`HOLDOUT_SEED`, which the search
never uses. ``accept_winner`` refuses by name (``HOLDOUT_SEED_NOT_HELD``) if the
two ever coincide, so this pair is checked at import time as well as there.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from config import REPO_ROOT
from services import workspaces
from services.agent import parameter_search, searched_behaviour_broker, sessions
from services.agent.search_runner import ScoringContext, VissimSearchRunner, runner_availability

logger = logging.getLogger(__name__)

#: Where a search job's per-candidate work dirs live. REPO_ROOT-relative, beside
#: ``runtime/simulations`` and ``runtime/agent_sessions``, and deliberately NOT
#: inside ``runtime/simulations``: a candidate run must never be mistaken for a
#: scenario the run picker can offer (it has no job record either, for the same
#: reason — see ``search_runner.VissimSearchRunner._score``).
SEARCH_ROOT = REPO_ROOT / "runtime" / "parameter_search"

# ---------------------------------------------------------------------------
# The bounds a request may not talk its way past
# ---------------------------------------------------------------------------

#: Wall-clock budget floor. Below a minute a search cannot finish even its
#: baseline run, so a smaller number is not a shorter search — it is a search
#: that reports PARTIAL having done nothing.
MIN_BUDGET_S = 60.0

#: Wall-clock budget ceiling: four hours. The engine stops cleanly at the budget
#: and reports PARTIAL, so the ceiling is not a correctness bound — it is the
#: bound on how long one POST can occupy the machine's only Vissim.
MAX_BUDGET_S = 4 * 3600.0

#: Simulated seconds per candidate. One hour, because the reality check compares
#: one hour of modelled volume against one hour of observed counts — that is why
#: it refuses a multi-seed run's summed volumes by name. Overridable per request
#: within the same bounds ``SimParams.sim_duration`` uses.
DEFAULT_DURATION_S = 3600
MIN_DURATION_S = 60
MAX_DURATION_S = 86400

#: The seed every calibration candidate runs at.
SEARCH_SEED = 42

#: The seed the winner and the baseline are re-run at for the holdout. Never
#: used by the search itself — that is the whole point, and ``accept_winner``
#: refuses the pair if it ever stops being true.
HOLDOUT_SEED = 4243

assert SEARCH_SEED != HOLDOUT_SEED, "the holdout seed must be one the search never used"

#: Job records kept in memory. Same reasoning as the brokers' bounds: a record
#: is useful between a start and the proposal that follows it.
MAX_JOBS = 16

#: Stated as data so a test can pin it and a reader can find it: resuming a
#: search from a file on disk is NOT a feature of this unit. The engine supports
#: it (``parameter_search.resume`` takes a state dict), the state IS written for
#: audit, and nothing here or anywhere else reads it back — because a grant may
#: only be minted from a search THIS PROCESS ran and remembered
#: (``searched_behaviour_broker``'s module docstring, L49-58).
RESUME_FROM_DISK = {
    "supported": False,
    "why": (
        "A grant is re-minted from what this process remembered, never from a file. A resumed "
        "state would have to be re-run and re-remembered in-process before it could authorise "
        "anything, so reading one back here would add a forgery surface and no capability."
    ),
    "artifact": "state.PARTIAL.NOT-MINTABLE.json",
}

# ---------------------------------------------------------------------------
# Statuses and refusal codes
# ---------------------------------------------------------------------------

QUEUED = "QUEUED"
RUNNING = "RUNNING"
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
JOB_STATUSES = (QUEUED, RUNNING, COMPLETE, PARTIAL, FAILED)

#: This machine cannot drive Vissim (not Windows, or pywin32 missing). The same
#: measurement ``runner_requirement()['wired']`` reports, refused here by name so
#: a client that rendered a start control anyway gets a sentence, not a 500.
RUNNER_NOT_WIRED = "RUNNER_NOT_WIRED"

#: No agent session was named. A search that finishes with nowhere to land is an
#: hour of Vissim whose winner no turn can propose — the broker is keyed by
#: session and ``remember`` answers ``None`` without one. Refused BEFORE the hour
#: is spent rather than discovered after it.
NO_AGENT_SESSION = "NO_AGENT_SESSION"

#: The named session is not this workspace's. Fail-closed, via the one ownership
#: predicate (``sessions.session_workspace_matches``).
SESSION_NOT_IN_WORKSPACE = "SESSION_NOT_IN_WORKSPACE"

#: No readable ``.inpx`` to clone candidates from.
NO_ACTIVE_NETWORK = "NO_ACTIVE_NETWORK"

#: More than one driving behaviour could be searched and the request named none.
#: The candidates are listed in the refusal's detail so the choice is a click.
BEHAVIOUR_NOT_CHOSEN = "BEHAVIOUR_NOT_CHOSEN"

#: The named behaviour is not in the network.
BEHAVIOUR_NOT_FOUND = "BEHAVIOUR_NOT_FOUND"

#: The behaviour exists but one of the three searched parameters cannot be read
#: off it, so a baseline vector cannot be formed and a write could not be
#: verified. Also the answer when NO behaviour in the network is readable.
BEHAVIOUR_NOT_READABLE = "BEHAVIOUR_NOT_READABLE"

#: A search is already running in this workspace. One Vissim, one search.
SEARCH_ALREADY_RUNNING = "SEARCH_ALREADY_RUNNING"

START_REFUSAL_CODES = (
    RUNNER_NOT_WIRED,
    NO_AGENT_SESSION,
    SESSION_NOT_IN_WORKSPACE,
    NO_ACTIVE_NETWORK,
    BEHAVIOUR_NOT_CHOSEN,
    BEHAVIOUR_NOT_FOUND,
    BEHAVIOUR_NOT_READABLE,
    SEARCH_ALREADY_RUNNING,
)

#: Terminal reasons a FINISHED job can carry beyond the search's own statuses.
BEHAVIOUR_GONE_AT_PROPOSAL = "BEHAVIOUR_GONE_AT_PROPOSAL"
NOT_REMEMBERED = "NOT_REMEMBERED"

#: The record was claimed but its thread never started (thread exhaustion). The
#: job is FAILED rather than QUEUED so the workspace is not wedged — see
#: :func:`start`.
JOB_NOT_STARTED = "JOB_NOT_STARTED"


def _text(ko: str, en: str, ja: str) -> dict[str, str]:
    return {"ko": ko, "en": en, "ja": ja}


def _refusal(code: str, *, ko: str, en: str, ja: str, detail: Any = None) -> dict[str, Any]:
    """One named refusal, in the gate's own shape (``code`` / ``why`` /
    ``opens_when`` is ``parameter_search._refusal``'s; this one carries ``why``
    and ``detail``, because every code here says what is wrong and what to do in
    the same sentence)."""
    return {"code": code, "why": _text(ko, en, ja), "detail": detail}


def already_running_refusal(job_id: Any = None) -> dict[str, Any]:
    """One sentence, ONE definition of it.

    Said in two places — :func:`evaluate_start`'s courteous early check and the
    route's handling of a lost atomic claim — and it must be the same sentence in
    both, because from the user's side those are the same event. A race that
    produced different words than the check would look like a different problem.
    """
    return _refusal(
        SEARCH_ALREADY_RUNNING,
        ko="이 프로젝트에서 이미 탐색이 실행 중입니다. Vissim은 한 번에 하나만 돌립니다.",
        en="A search is already running in this project. One Vissim instance, one search.",
        ja="このプロジェクトで既に探索が実行中です。Vissimは一度に一つだけ実行します。",
        detail={"job_id": job_id},
    )


def job_not_started_refusal(error: Any = None) -> dict[str, Any]:
    """The thread could not be started at all. Named, so the screen says so.

    A rare, real operating condition (thread exhaustion) whose worst outcome is
    not the failure but the LIE that would follow it — a record left QUEUED reads
    as "search running" forever. See :func:`start`.
    """
    return _refusal(
        JOB_NOT_STARTED,
        ko="탐색 작업을 시작할 수 없었습니다(백엔드가 새 스레드를 만들지 못했습니다). "
        "잠시 후 다시 시도해 주세요 — 아무 것도 실행되지 않았습니다.",
        en="The search job could not be started (the backend could not create a new thread). "
        "Try again in a moment — nothing was run.",
        ja="探索ジョブを開始できませんでした(バックエンドが新しいスレッドを作成できませんでした)。"
        "少し待って再試行してください — 何も実行されていません。",
        detail={"error": str(error) if error else None},
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp_budget_s(value: Any) -> tuple[float, bool]:
    """``(budget, was_clamped)`` — never a raise, never an unbounded number.

    A malformed or missing value is the DEFAULT, not an error: the field is an
    override, and the product's answer to "I could not read your number" is the
    number the card already showed. ``was_clamped`` is reported on the job record
    so a user who asked for ten hours can see they were given four.
    """
    try:
        requested = float(value)
    except (TypeError, ValueError):
        return float(parameter_search.DEFAULT_BUDGET_S), False
    if requested != requested or requested in (float("inf"), float("-inf")):  # NaN / inf
        return float(parameter_search.DEFAULT_BUDGET_S), False
    clamped = max(MIN_BUDGET_S, min(MAX_BUDGET_S, requested))
    return float(clamped), clamped != requested


def clamp_duration_s(value: Any) -> tuple[int, bool]:
    """``(simulated seconds per candidate, was_clamped)``. See :func:`clamp_budget_s`."""
    try:
        requested = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_DURATION_S, False
    clamped = max(MIN_DURATION_S, min(MAX_DURATION_S, requested))
    return int(clamped), clamped != requested


# ---------------------------------------------------------------------------
# The target behaviour
# ---------------------------------------------------------------------------


def searchable_behaviours(shadow_model: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Every driving behaviour a search could actually be run and proposed on.

    A behaviour qualifies only when all three searched parameters are READABLE
    off it. That is not a nicety: the baseline vector is built from those values
    (``parameter_search.start``), every candidate write is verified by reading
    the same fields back, and ``propose`` names the behaviour the winner is
    written to. A behaviour with an unreadable field could be searched only by
    inventing its starting point.

    Returned as data — number, name, reference count and the three current
    values — so the screen can offer the choice instead of this module guessing
    it, and so a refusal can NAME what the caller could have picked.
    """
    payload = shadow_model if isinstance(shadow_model, Mapping) else {}
    behaviours = payload.get("driving_behaviors")
    if not isinstance(behaviours, list):
        return []
    candidates: list[dict[str, Any]] = []
    for behaviour in behaviours:
        if not isinstance(behaviour, Mapping):
            continue
        values = {
            name: behaviour.get(name) for name in parameter_search.SEARCHABLE_PARAMETERS
        }
        if any(value is None for value in values.values()):
            continue
        candidates.append(
            {
                "no": str(behaviour.get("no") or ""),
                "name": str(behaviour.get("name") or ""),
                "reference_count": behaviour.get("reference_count"),
                "params": {name: float(value) for name, value in values.items()},
            }
        )
    return [item for item in candidates if item["no"]]


def _behaviour_record(
    shadow_model: Mapping[str, Any] | None, behavior_no: str
) -> dict[str, Any] | None:
    payload = shadow_model if isinstance(shadow_model, Mapping) else {}
    for behaviour in payload.get("driving_behaviors") or ():
        if isinstance(behaviour, Mapping) and str(behaviour.get("no") or "") == str(behavior_no):
            return dict(behaviour)
    return None


def resolve_target_behaviour(
    shadow_model: Mapping[str, Any] | None, behavior_no: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(behaviour, refusal)`` — exactly one of the two is ``None``.

    With a ``behavior_no``: that behaviour or a named refusal. Without one: the
    single searchable behaviour if there is EXACTLY one, and
    :data:`BEHAVIOUR_NOT_CHOSEN` otherwise. Auto-picking from several would be
    this module choosing which of a network's behaviours a modeller meant, on no
    evidence — and every candidate run and the final write would inherit that
    guess.
    """
    candidates = searchable_behaviours(shadow_model)
    if behavior_no:
        record = _behaviour_record(shadow_model, behavior_no)
        if record is None:
            return None, _refusal(
                BEHAVIOUR_NOT_FOUND,
                ko=f"주행행태 {behavior_no}번이 현재 네트워크에 없습니다.",
                en=f"Driving behaviour {behavior_no} is not in the active network.",
                ja=f"走行挙動{behavior_no}番が現在のネットワークにありません。",
                detail={"behaviours": candidates},
            )
        if not any(item["no"] == str(behavior_no) for item in candidates):
            return None, _refusal(
                BEHAVIOUR_NOT_READABLE,
                ko=f"주행행태 {behavior_no}번에서 탐색 대상 3개 파라미터를 모두 읽을 수 없습니다. "
                "시작값을 만들 수 없으므로 탐색을 시작하지 않습니다.",
                en=f"Driving behaviour {behavior_no} does not carry all three searched parameters "
                "readably, so no baseline vector can be formed and no search starts.",
                ja=f"走行挙動{behavior_no}番から探索対象の3パラメータをすべて読み取れません。"
                "開始値を作れないため探索を開始しません。",
                detail={"behaviours": candidates},
            )
        return record, None

    if len(candidates) == 1:
        return _behaviour_record(shadow_model, candidates[0]["no"]), None
    if not candidates:
        return None, _refusal(
            BEHAVIOUR_NOT_READABLE,
            ko="탐색 대상 3개 파라미터를 모두 읽을 수 있는 주행행태가 네트워크에 없습니다.",
            en="No driving behaviour in this network carries all three searched parameters readably.",
            ja="探索対象の3パラメータをすべて読み取れる走行挙動がネットワークにありません。",
            detail={"behaviours": []},
        )
    return None, _refusal(
        BEHAVIOUR_NOT_CHOSEN,
        ko=f"탐색 가능한 주행행태가 {len(candidates)}개입니다. 어느 것을 보정할지 골라 주세요 — "
        "여러 개 중 하나를 이 시스템이 임의로 고르지 않습니다.",
        en=f"{len(candidates)} driving behaviours could be searched. Pick the one to calibrate — "
        "this system does not choose one of several on your behalf.",
        ja=f"探索可能な走行挙動が{len(candidates)}件あります。どれを補正するか選んでください — "
        "複数の中から本システムが任意に選ぶことはありません。",
        detail={"behaviours": candidates},
    )


# ---------------------------------------------------------------------------
# The in-process job registry
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_JOBS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def reset() -> None:
    """Drop every remembered job. For tests; production never needs it."""
    with _LOCK:
        _JOBS.clear()


def _claim(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Insert a QUEUED record, or answer ``None`` because one is already live here.

    CHECK AND INSERT UNDER ONE LOCK. ``evaluate_start`` also asks whether a
    search is running, and that answer is what a user reads — but two POSTs that
    arrive together would both find nothing running and both enqueue, and the
    second Vissim instance is exactly the configuration this codebase has never
    verified. So the decision that MATTERS is made here, where it is atomic, and
    ``evaluate_start``'s copy is the courteous early one.
    """
    workspace_id = str(record.get("workspace_id") or "")
    with _LOCK:
        for existing in _JOBS.values():
            if str(existing.get("workspace_id") or "") != workspace_id:
                continue
            if existing.get("status") in (QUEUED, RUNNING):
                return None
        stored = dict(record)
        _JOBS[str(stored["id"])] = stored
        _JOBS.move_to_end(str(stored["id"]))
        while len(_JOBS) > MAX_JOBS:
            _JOBS.popitem(last=False)
        return dict(stored)


def _update(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """Merge into one record and return a COPY. A live reference into the store
    would let a request handler serialize a dict the job thread is mid-write on
    (``routers/simulations._update_job``'s own reason for the same shape)."""
    with _LOCK:
        record = _JOBS.get(str(job_id))
        if record is None:
            return None
        record.update(fields)
        record["last_update_at"] = _now_iso()
        return dict(record)


def get_job(job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    with _LOCK:
        record = _JOBS.get(str(job_id))
        return None if record is None else dict(record)


def latest_job(workspace_id: str | None = None) -> dict[str, Any] | None:
    """The newest job THIS workspace started, or ``None``.

    Workspace-scoped like every other listing in this codebase: another
    project's search must be indistinguishable from no search at all.
    """
    target = str(workspace_id) if workspace_id else None
    if target is None:
        try:
            target = workspaces.current_workspace_id()
        except Exception:  # noqa: BLE001 — no workspace bound means nothing to show
            return None
    with _LOCK:
        for record in reversed(list(_JOBS.values())):
            if str(record.get("workspace_id") or "") == target:
                return dict(record)
    return None


def _running_job(workspace_id: str) -> dict[str, Any] | None:
    with _LOCK:
        for record in reversed(list(_JOBS.values())):
            if str(record.get("workspace_id") or "") != str(workspace_id):
                continue
            if record.get("status") in (QUEUED, RUNNING):
                return dict(record)
    return None


# ---------------------------------------------------------------------------
# The gate — server-derived, every time
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartPlan:
    """Everything the job thread needs, all of it derived HERE.

    Frozen and complete on purpose: once this exists, the thread reads nothing
    from the request again. In particular ``preconditions`` is the payload THIS
    process computed from artifacts it read, so ``parameter_search.start``'s
    refusal-on-a-failed-gate and its re-derivation of the holdout split are
    protecting a payload no client ever touched.
    """

    preconditions: dict[str, Any]
    behaviour: dict[str, Any]
    baseline_vector: dict[str, float]
    scoring: ScoringContext
    budget_s: float
    budget_clamped: bool
    duration_s: int
    duration_clamped: bool
    session_id: str
    workspace_id: str
    source_inpx: Path
    scenario_id: str | None


def evaluate_start(
    *,
    reality_check: Mapping[str, Any] | None,
    applied_rows: Sequence[Mapping[str, Any]] | None,
    rail: Mapping[str, Any] | None,
    shadow_model: Mapping[str, Any] | None,
    source_inpx: Path | None,
    workspace_id: str,
    session_id: str | None,
    behavior_no: str | None = None,
    budget_s: Any = None,
    duration_s: Any = None,
    scenario_id: str | None = None,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
    targets: Sequence[Any] | None = None,
    sessions_root: Path | None = None,
) -> dict[str, Any]:
    """May a search start here, now? Every "no" is a named code.

    Returns ``{"ok", "refusals", "preconditions", "behaviours", "budget",
    "duration", "plan"}``. ``plan`` is a :class:`StartPlan` when ``ok``, and
    ``None`` otherwise.

    ``refusals`` collects BOTH families in one list — this module's own start
    refusals and ``evaluate_preconditions``'s gate refusals — and it collects
    them rather than short-circuiting, for the reason that function gives: a
    modeller three steps away should learn all three now.

    The evidence arguments are the caller's, and every one of them is something
    the SERVER read (``routers/calibration.py`` gathers exactly the same bundle
    for ``GET /rail``). Nothing on the request body appears here except
    selectors: which run to score against, which observation window, which
    behaviour, how much budget.

    ``targets`` is server-derived too (``routers/calibration._derived_targets``)
    and is FROZEN onto the plan rather than re-derived on the job thread: the
    same objects the gate's report was built against are the ones every
    candidate is scored against, so the search cannot end up optimising one
    account of the network while the gate was opened on another.
    """
    refusals: list[dict[str, Any]] = []

    available, unavailable_reason = runner_availability()
    if not available:
        refusals.append(
            _refusal(
                RUNNER_NOT_WIRED,
                ko="이 백엔드에서는 Vissim COM을 쓸 수 없어 탐색을 실행할 수 없습니다.",
                en="This backend cannot drive Vissim COM, so no search can be run here.",
                ja="このバックエンドではVissim COMを使えないため探索を実行できません。",
                detail={"reason": unavailable_reason},
            )
        )

    resolved_session = str(session_id or "").strip()
    if not resolved_session:
        refusals.append(
            _refusal(
                NO_AGENT_SESSION,
                ko="AI 드로어에서 대화를 먼저 시작해 주세요. 탐색 결과는 그 대화에 기록되고, "
                "대화가 없으면 승자를 제안으로 이어갈 수 없습니다.",
                en="Start a conversation in the AI drawer first. A finished search is remembered "
                "against that session, and without one its winner cannot become a proposal.",
                ja="先にAIドロワーで会話を開始してください。探索結果はその会話に記録され、"
                "会話がなければ勝者を提案につなげられません。",
            )
        )
    elif not sessions.session_workspace_matches(
        resolved_session, workspace_id, sessions_root
    ):
        refusals.append(
            _refusal(
                SESSION_NOT_IN_WORKSPACE,
                ko="지정한 대화 세션은 이 프로젝트의 것이 아닙니다.",
                en="That conversation session does not belong to this project.",
                ja="指定された会話セッションはこのプロジェクトのものではありません。",
            )
        )

    network = Path(source_inpx) if source_inpx else None
    if network is None or not network.is_file():
        refusals.append(
            _refusal(
                NO_ACTIVE_NETWORK,
                ko="현재 프로젝트에 읽을 수 있는 네트워크(.inpx)가 없습니다.",
                en="This project has no readable network (.inpx) to clone candidates from.",
                ja="現在のプロジェクトに読み取り可能なネットワーク(.inpx)がありません。",
            )
        )

    running = _running_job(workspace_id)
    if running is not None:
        refusals.append(already_running_refusal(running.get("id")))

    behaviour, behaviour_refusal = resolve_target_behaviour(shadow_model, behavior_no)
    if behaviour_refusal is not None:
        refusals.append(behaviour_refusal)

    # THE GATE. Re-derived here from the server's own artifacts — never read off
    # the request, and never cached from the last /rail call either (the run it
    # describes may have been re-scored since).
    preconditions = parameter_search.evaluate_preconditions(
        reality_check=reality_check,
        applied_rows=applied_rows,
        rail=rail,
    )
    for item in preconditions["refusals"]:
        refusals.append(
            {
                "code": item["code"],
                "why": item["why"],
                "opens_when": item["opens_when"],
                "detail": item.get("detail"),
            }
        )

    budget, budget_clamped = clamp_budget_s(budget_s)
    duration, duration_clamped = clamp_duration_s(duration_s)
    candidates = searchable_behaviours(shadow_model)
    answer: dict[str, Any] = {
        "ok": not refusals,
        "refusals": refusals,
        "preconditions": preconditions,
        "behaviours": candidates,
        "budget": {
            "budget_s": budget,
            "clamped": budget_clamped,
            "min_s": MIN_BUDGET_S,
            "max_s": MAX_BUDGET_S,
            "default_s": parameter_search.DEFAULT_BUDGET_S,
        },
        "duration": {
            "duration_s": duration,
            "clamped": duration_clamped,
            "min_s": MIN_DURATION_S,
            "max_s": MAX_DURATION_S,
            "default_s": DEFAULT_DURATION_S,
        },
        "plan": None,
    }
    if refusals or behaviour is None or network is None:
        return answer

    answer["plan"] = StartPlan(
        preconditions=dict(preconditions),
        behaviour=dict(behaviour),
        baseline_vector={
            name: float(behaviour[name]) for name in parameter_search.SEARCHABLE_PARAMETERS
        },
        scoring=ScoringContext(
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
            targets=tuple(targets or ()),
        ),
        budget_s=budget,
        budget_clamped=budget_clamped,
        duration_s=duration,
        duration_clamped=duration_clamped,
        session_id=resolved_session,
        workspace_id=str(workspace_id),
        source_inpx=network,
        scenario_id=scenario_id,
    )
    return answer


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def default_runner_factory(plan: StartPlan, work_dir: Path, on_candidate) -> Any:
    """The real runner, built from the plan. What production always uses."""
    return VissimSearchRunner(
        source_inpx=plan.source_inpx,
        work_dir=work_dir,
        behavior_no=int(plan.behaviour["no"]),
        duration_s=plan.duration_s,
        scoring=plan.scoring,
        on_candidate=on_candidate,
        label=f"parameter-search:{work_dir.name}",
    )


#: How the job builds its runner, resolved at call time inside the thread body.
#:
#: A MODULE ATTRIBUTE rather than a ``start()`` parameter, deliberately: tests
#: swap it for a synthetic runner, and a parameter would mean a route could be
#: handed one — by accident, or by a future signature that forwards a request
#: field into it. Nothing reachable from HTTP can name this.
runner_factory: Callable[..., Any] = default_runner_factory


def start(plan: StartPlan, *, work_root: Path | None = None) -> dict[str, Any] | None:
    """Enqueue one search, or ``None`` because this workspace already has one live.

    The record exists BEFORE the thread does, so a caller can never be handed a
    job id that names nothing. The workspace is captured on the submitting thread
    (in :func:`evaluate_start`) and re-bound inside the thread body — ContextVars
    do not cross ``threading.Thread`` boundaries, which is the trap
    ``_run_agent_simulation_job`` documents at its own seam.

    ``None`` rather than a raise: "another search is already running" is a named
    ANSWER (:data:`SEARCH_ALREADY_RUNNING`), and the route turns it back into the
    same refusal ``evaluate_start`` would have produced a moment earlier. The
    claim is atomic (see :func:`_claim`), so this is the check that actually
    guarantees one Vissim at a time.

    A returned record may ALREADY be terminal: if the thread could not be started
    it comes back FAILED with :data:`JOB_NOT_STARTED`. Callers must read
    ``status`` rather than assume a non-``None`` record means a running search —
    ``routers/calibration.start_parameter_search`` does, and reports
    ``started: false``.
    """
    job_id = uuid.uuid4().hex[:8]
    root = Path(work_root) if work_root is not None else SEARCH_ROOT
    work_dir = root / job_id
    record = _claim(
        {
            "id": job_id,
            "workspace_id": plan.workspace_id,
            "session_id": plan.session_id,
            "status": QUEUED,
            "phase": "queued",
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "last_update_at": _now_iso(),
            "budget_s": plan.budget_s,
            "budget_clamped": plan.budget_clamped,
            "duration_s": plan.duration_s,
            "duration_clamped": plan.duration_clamped,
            "seed": SEARCH_SEED,
            "holdout_seed": HOLDOUT_SEED,
            "behavior_no": str(plan.behaviour.get("no") or ""),
            "behavior_name": str(plan.behaviour.get("name") or ""),
            "scenario_id": plan.scenario_id,
            # ``as_record``, not ``as_kwargs``: the kwargs carry pydantic target
            # objects for the report builder, and a job record must stay a
            # payload something could serialise.
            "scoring": plan.scoring.as_record(),
            "calibration_nodes": list(
                (plan.preconditions.get("split") or {}).get("calibration") or []
            ),
            "holdout_nodes": list((plan.preconditions.get("split") or {}).get("holdout") or []),
            "candidates_evaluated": 0,
            "candidates_failed": 0,
            "consumed_s": 0.0,
            "elapsed_s": 0.0,
            "search_status": None,
            "terminal_reason": None,
            "acceptance_code": None,
            "proposal_outcome": None,
            "proposal_reason_code": None,
            "winner": None,
            "search_id": None,
            "remembered": False,
            "partial_state_path": None,
            "error": None,
            "work_dir": str(work_dir),
        }
    )
    if record is None:
        return None
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, plan, work_dir),
        name=f"parameter-search-{job_id}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as exc:  # noqa: BLE001 — see below; this MUST NOT wedge the queue
        # THE SIBLING OF THE QUEUED WEDGE (2026-08-21, refuter). The claim above
        # is already committed when this line runs, so a ``thread.start()`` that
        # fails — ``RuntimeError("can't start new thread")`` under thread
        # exhaustion is the realistic one — leaves a QUEUED record in ``_JOBS``
        # that no thread will ever advance. ``_claim`` treats QUEUED as live, so
        # that workspace's start button is dead until the process restarts, and
        # the screen reads "search running" forever over a search that never
        # began. The discipline ``_run_job`` states for its own exits applies
        # here too: EVERY exit from this function leaves a terminal status behind
        # or a live thread that will write one.
        logger.exception("Parameter-search job %s could not be started", job_id)
        return _update(
            job_id,
            status=FAILED,
            phase="error",
            finished_at=_now_iso(),
            terminal_reason=JOB_NOT_STARTED,
            error=f"{type(exc).__name__}: {exc}",
        )
    return record


def _run_job(job_id: str, plan: StartPlan, work_dir: Path) -> None:
    """Thread body. Re-binds the submitting request's workspace and nothing else.

    The bind is wrapped because a failure HERE is outside
    :func:`_run_job_bound`'s own handler, and a job that never reached a terminal
    status would sit at QUEUED forever — which, since :func:`_claim` treats
    QUEUED as live, would wedge this workspace's start button permanently.
    MEASURED 2026-08-21: a malformed workspace id did exactly that. Every exit
    from this thread must leave a status behind.
    """
    try:
        with workspaces.bind_workspace(plan.workspace_id):
            _run_job_bound(job_id, plan, work_dir)
    except Exception as exc:  # noqa: BLE001 — a thread that dies must still report
        logger.exception("Parameter-search job %s could not bind its workspace", job_id)
        _update(
            job_id,
            status=FAILED,
            phase="error",
            finished_at=_now_iso(),
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_job_bound(job_id: str, plan: StartPlan, work_dir: Path) -> None:
    started = time.monotonic()
    coinitialized = False
    try:
        # COM is apartment-threaded: every thread that touches it initializes
        # once. Absent pywin32 this is simply skipped — ``evaluate_start``
        # already refused, and a synthetic runner (tests) needs no COM at all.
        try:
            import pythoncom

            pythoncom.CoInitialize()
            coinitialized = True
        except Exception:  # noqa: BLE001 — no COM on this box; the runner reports it
            coinitialized = False

        work_dir.mkdir(parents=True, exist_ok=True)
        _update(job_id, status=RUNNING, phase="searching", started_at=_now_iso())

        def on_candidate(progress: Mapping[str, Any]) -> None:
            record = get_job(job_id) or {}
            failed = int(record.get("candidates_failed") or 0)
            _update(
                job_id,
                candidates_evaluated=int(progress.get("index") or 0),
                candidates_failed=failed + (0 if progress.get("ok") else 1),
                elapsed_s=time.monotonic() - started,
                last_candidate={
                    "index": progress.get("index"),
                    "seed": progress.get("seed"),
                    "ok": progress.get("ok"),
                    "reason": progress.get("reason"),
                },
            )

        runner = runner_factory(plan, work_dir, on_candidate)
        state = parameter_search.start(
            search_id=f"psearch-{job_id}",
            preconditions=plan.preconditions,
            baseline_vector=plan.baseline_vector,
            seed=SEARCH_SEED,
            budget_s=plan.budget_s,
        )
        search = parameter_search.ParameterSearch(state=state, runner=runner)
        result = search.run()

        if result["status"] != parameter_search.STATUS_COMPLETE:
            _finish_partial(job_id, search, result, work_dir, started)
            return

        _update(job_id, phase="holdout", elapsed_s=time.monotonic() - started)
        acceptance = parameter_search.accept_winner(search, runner, unseen_seed=HOLDOUT_SEED)
        # RE-SNAPSHOT AFTER ACCEPTANCE. ``accept_winner`` appends the two HOLDOUT
        # rows to the live state, and ``grant_from_search`` re-derives the
        # acceptance FROM THE LEDGER — so a result captured before this point
        # carries a ledger that cannot support the grant, and the broker would
        # refuse it. Same order, same reason, as the #51-b fixture's own note.
        final = search.result(parameter_search.STATUS_COMPLETE)

        behaviour = _behaviour_now(plan)
        if behaviour is None:
            _update(
                job_id,
                status=COMPLETE,
                phase="done",
                finished_at=_now_iso(),
                elapsed_s=time.monotonic() - started,
                consumed_s=float(final.get("consumed_s") or 0.0),
                search_status=final["status"],
                acceptance_code=acceptance.get("code"),
                terminal_reason=BEHAVIOUR_GONE_AT_PROPOSAL,
                winner=_winner_summary(final),
            )
            return

        proposal = parameter_search.propose(
            result=final, acceptance=acceptance, behavior=behaviour
        )
        search_id = _remember(plan, final, acceptance, proposal)
        _update(
            job_id,
            status=COMPLETE,
            phase="done",
            finished_at=_now_iso(),
            elapsed_s=time.monotonic() - started,
            consumed_s=float(final.get("consumed_s") or 0.0),
            search_status=final["status"],
            acceptance_code=acceptance.get("code"),
            proposal_outcome=proposal.get("outcome"),
            proposal_reason_code=proposal.get("reason_code"),
            winner=_winner_summary(final),
            search_id=search_id,
            remembered=bool(search_id),
            terminal_reason=None if search_id else NOT_REMEMBERED,
        )
    except Exception as exc:  # noqa: BLE001 — a dead thread must still leave a reason
        logger.exception("Parameter-search job %s failed", job_id)
        _update(
            job_id,
            status=FAILED,
            phase="error",
            finished_at=_now_iso(),
            elapsed_s=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if coinitialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                logger.exception("Could not CoUninitialize the parameter-search thread")


def _behaviour_now(plan: StartPlan) -> dict[str, Any] | None:
    """Re-read the target behaviour at PROPOSAL time.

    #51-b's consent radius is the blast radius "as it stands at proposal time",
    and it is the number both decisions' safety argument rests on — so it is read
    when the proposal is built, not carried over from an hour earlier. The
    candidates all ran on clones, so the active network has not moved under us;
    this is belt and braces, and a behaviour that HAS disappeared produces a
    named terminal reason instead of a proposal about an object that is gone.
    """
    from services.agent.tools_network import build_shadow_model

    try:
        shadow = build_shadow_model(plan.source_inpx)
    except Exception as exc:  # noqa: BLE001 — an unparseable network is not a crash
        logger.warning(
            "parameter search: could not re-read the network at proposal time (%s: %s)",
            type(exc).__name__,
            exc,
        )
        return None
    return _behaviour_record(shadow, str(plan.behaviour.get("no") or ""))


def _remember(
    plan: StartPlan,
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> str | None:
    """Hand the finished search to the broker, ON THIS THREAD, in this session.

    ``set_active_session`` published in a ``try``/``finally`` around the call is
    ``routers/assistant.py``'s exact shape (its turn thread does the same for
    both broker species). It matters here for the same reason it matters there:
    the store is keyed per session regardless, so an unset pointer means nothing
    is remembered — never that one session's search becomes visible to another —
    and clearing it in ``finally`` stops a later, unrelated piece of work on a
    reused thread from writing into this session's store.

    ``remember`` returns ``None`` when the payloads are not ones
    ``grant_from_search`` accepts — a refused holdout, a partial status, a
    proposal with no actions. That is not an error here: it is the honest answer
    that this search produced nothing a human could be asked to approve, and it
    lands on the job record as ``remembered: false``.
    """
    searched_behaviour_broker.set_active_session(plan.session_id)
    try:
        return searched_behaviour_broker.remember(
            result=result,
            acceptance=acceptance,
            proposal=proposal,
            preconditions=plan.preconditions,
            session_id=plan.session_id,
        )
    finally:
        searched_behaviour_broker.set_active_session(None)


def _finish_partial(
    job_id: str,
    search: parameter_search.ParameterSearch,
    result: Mapping[str, Any],
    work_dir: Path,
    started: float,
) -> None:
    """A budget-exhausted search: audited, and deliberately NOT remembered.

    Nothing on this path touches the broker. The state file exists so a human can
    see what the hour bought, and its NAME says what it is not — see
    :data:`RESUME_FROM_DISK`. It is written best-effort: a search that ran
    correctly and could not write its audit copy is still a search that ran
    correctly, and the failure is logged rather than turned into a job error.
    """
    path: str | None = None
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        artifact = work_dir / str(RESUME_FROM_DISK["artifact"])
        artifact.write_text(
            json.dumps(
                {
                    "NOT_MINTABLE": RESUME_FROM_DISK["why"],
                    "resume_from_disk": RESUME_FROM_DISK,
                    "status": result.get("status"),
                    "state": search.state.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        path = str(artifact)
    except OSError as exc:
        logger.warning("Could not write the partial search audit artifact: %s", exc)

    _update(
        job_id,
        status=PARTIAL,
        phase="done",
        finished_at=_now_iso(),
        elapsed_s=time.monotonic() - started,
        consumed_s=float(result.get("consumed_s") or 0.0),
        search_status=result.get("status"),
        terminal_reason=result.get("status"),
        winner=_winner_summary(result),
        partial_state_path=path,
        remembered=False,
        search_id=None,
    )


def _winner_summary(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """The best PROPOSABLE row, reduced to numbers a card can print.

    ``best`` is already the admissibility-filtered row (``ParameterSearch.best``),
    so this never shows a point the search would refuse to propose. ``None`` when
    the search found none — which a partial search very often has.
    """
    best = result.get("best")
    if not isinstance(best, Mapping):
        return None
    return {
        "params": {str(k): v for k, v in dict(best.get("params") or {}).items()},
        "objective": best.get("objective"),
        "pass_count": best.get("pass_count"),
        "graded": best.get("graded"),
        "baseline_objective": (result.get("baseline") or {}).get("objective")
        if isinstance(result.get("baseline"), Mapping)
        else None,
    }


# ---------------------------------------------------------------------------
# The card the rail renders
# ---------------------------------------------------------------------------

_STATUS_TEXT = {
    QUEUED: _text("대기 중", "Queued", "待機中"),
    RUNNING: _text("실행 중", "Running", "実行中"),
    COMPLETE: _text("완료", "Finished", "完了"),
    PARTIAL: _text("예산 소진(부분 결과)", "Budget spent (partial)", "予算消化(部分結果)"),
    FAILED: _text("실패", "Failed", "失敗"),
}


def job_card(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One job as the ⑫ track shows it — every word server-side.

    The client renders these fields and composes none of them: there is no
    client-side status vocabulary for a search, no client-side elapsed
    arithmetic, and no client-side opinion about whether a finished search
    produced anything. ``headline`` is the one sentence the track prints, and
    which sentence it is depends only on fields decided on the job thread.
    """
    if not isinstance(record, Mapping) or not record.get("id"):
        return None
    status = str(record.get("status") or QUEUED)
    evaluated = int(record.get("candidates_evaluated") or 0)
    budget_s = float(record.get("budget_s") or 0.0)
    elapsed_s = float(record.get("elapsed_s") or 0.0)
    minutes = int(round(budget_s / 60.0))
    winner = record.get("winner") if isinstance(record.get("winner"), Mapping) else None

    if status in (QUEUED, RUNNING):
        headline = _text(
            f"탐색 실행 중 — 후보 {evaluated}개 평가, 예산 {minutes}분 중 "
            f"{int(round(elapsed_s / 60.0))}분 사용.",
            f"Search running — {evaluated} candidate(s) evaluated, "
            f"{int(round(elapsed_s / 60.0))} of {minutes} budget minutes used.",
            f"探索実行中 — 候補{evaluated}件を評価、予算{minutes}分のうち"
            f"{int(round(elapsed_s / 60.0))}分を使用。",
        )
    elif status == PARTIAL:
        headline = _text(
            f"예산을 다 썼습니다 — 후보 {evaluated}개를 평가했지만 탐색이 끝나지 않았습니다. "
            "부분 결과는 승인 대상이 되지 않습니다.",
            f"The budget ran out — {evaluated} candidate(s) were evaluated but the search did not "
            "finish. A partial result is not something that can be approved.",
            f"予算を使い切りました — 候補{evaluated}件を評価しましたが探索は完了していません。"
            "部分結果は承認対象になりません。",
        )
    elif status == FAILED:
        headline = _text(
            f"탐색이 실패했습니다: {record.get('error') or '원인 미상'}",
            f"The search failed: {record.get('error') or 'no reason recorded'}",
            f"探索が失敗しました: {record.get('error') or '原因不明'}",
        )
    elif record.get("remembered"):
        headline = _text(
            f"탐색 완료 — 후보 {evaluated}개 평가, 승자가 홀드아웃을 통과했습니다. "
            "AI 드로어에서 적용을 요청하면 승인 카드가 나옵니다.",
            f"Search finished — {evaluated} candidate(s) evaluated and the winner passed its "
            "holdout. Ask the AI drawer to apply it and the approval card appears there.",
            f"探索完了 — 候補{evaluated}件を評価し、勝者がホールドアウトを通過しました。"
            "AIドロワーで適用を依頼すると承認カードが出ます。",
        )
    else:
        headline = _text(
            f"탐색 완료 — 제안할 승자가 없습니다({record.get('acceptance_code') or '사유 없음'}).",
            f"Search finished with no winner to propose "
            f"({record.get('acceptance_code') or 'no reason recorded'}).",
            f"探索完了 — 提案できる勝者がありません({record.get('acceptance_code') or '事由なし'})。",
        )

    return {
        "id": record.get("id"),
        "status": status,
        # Is this job still going? Sent as a FIELD rather than left for the
        # client to derive from ``status``, because the client's one job here is
        # to render server words — the moment it owns the rule "QUEUED and
        # RUNNING mean keep polling" it owns a status vocabulary of its own, and
        # a fifth status added here would be silently treated as finished.
        "live": status in (QUEUED, RUNNING),
        "status_text": _STATUS_TEXT.get(status, _STATUS_TEXT[QUEUED]),
        "headline": headline,
        "phase": record.get("phase"),
        "candidates_evaluated": evaluated,
        "candidates_failed": int(record.get("candidates_failed") or 0),
        "budget_s": budget_s,
        "elapsed_s": elapsed_s,
        "consumed_s": float(record.get("consumed_s") or 0.0),
        "duration_s": record.get("duration_s"),
        "seed": record.get("seed"),
        "holdout_seed": record.get("holdout_seed"),
        "behavior_no": record.get("behavior_no"),
        "behavior_name": record.get("behavior_name"),
        "session_id": record.get("session_id"),
        "search_id": record.get("search_id"),
        "remembered": bool(record.get("remembered")),
        "terminal_reason": record.get("terminal_reason"),
        "acceptance_code": record.get("acceptance_code"),
        "proposal_outcome": record.get("proposal_outcome"),
        "winner": winner,
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "resume_from_disk": RESUME_FROM_DISK,
    }


__all__ = [
    "BEHAVIOUR_GONE_AT_PROPOSAL",
    "BEHAVIOUR_NOT_CHOSEN",
    "BEHAVIOUR_NOT_FOUND",
    "BEHAVIOUR_NOT_READABLE",
    "COMPLETE",
    "DEFAULT_DURATION_S",
    "FAILED",
    "HOLDOUT_SEED",
    "JOB_NOT_STARTED",
    "JOB_STATUSES",
    "MAX_BUDGET_S",
    "MAX_DURATION_S",
    "MAX_JOBS",
    "MIN_BUDGET_S",
    "MIN_DURATION_S",
    "NOT_REMEMBERED",
    "NO_ACTIVE_NETWORK",
    "NO_AGENT_SESSION",
    "PARTIAL",
    "QUEUED",
    "RESUME_FROM_DISK",
    "RUNNER_NOT_WIRED",
    "RUNNING",
    "SEARCH_ALREADY_RUNNING",
    "SEARCH_ROOT",
    "SEARCH_SEED",
    "SESSION_NOT_IN_WORKSPACE",
    "START_REFUSAL_CODES",
    "StartPlan",
    "already_running_refusal",
    "clamp_budget_s",
    "job_not_started_refusal",
    "clamp_duration_s",
    "default_runner_factory",
    "evaluate_start",
    "get_job",
    "job_card",
    "latest_job",
    "reset",
    "resolve_target_behaviour",
    "runner_factory",
    "searchable_behaviours",
    "start",
]

"""``/api/calibration`` — the calibration workspace's read side (#50) and, since
2026-08-21, the ONE control on it: ``POST /parameter-search/start`` (#51).

Every route here was a GET, every one of them pure with respect to this
product's state, and not one of them decided anything about a network. They
exist because four things a modeller must see were, until this unit, reachable
only by talking to the agent in the drawer or by reading a terminal:

===========================  ================================================
what                         where it lived before
===========================  ================================================
the run-vs-observed report    an agent tool (``run_reality_check``), delivered
(GEH per node, proposals,     as NDJSON ``step`` events on a live LLM turn and
refusals, D4 verdicts)        never persisted
the #43 diagnosis rung        a nine-field SLICE of the above, grafted onto the
                              same NDJSON stream
#44's ``verify_applied_row``  nothing called it outside its own tests
the pending approval          a single-use file only the approve endpoint read
===========================  ================================================

None of that logic moves. This module CALLS the existing services and returns
what they say:

* ``services.agent.tools_metrics.build_reality_check_report`` — no COM, no
  writes, states so in its own docstring
* ``services.agent.saturation_flow.verify_applied_row`` — pure, a READER by
  construction
* ``services.calibration_rail.derive_rail`` — pure status aggregation over the
  above plus the gate/health/phase-diagram artifacts
* ``services.agent.tools_network.build_shadow_model`` — the offline ``.inpx``
  parse, mtime-cached, no COM

WHAT THIS MODULE DELIBERATELY DOES NOT DO
------------------------------------------
**It adds no write path and no second approval machine.** The calibration
screen approves through the endpoint that already exists,
``POST /api/assistant/agent/approve`` — same single-use file, same
``require_approver`` identity check, same re-minted grant. All this module adds
is the ability to SEE that a pause is waiting without having been the browser
tab that opened the stream, and :func:`list_pending_approvals` is careful to
read the pending file without consuming it: deleting it is the approve leg's
act, and a viewer that consumed an approval by looking at it would be a new
failure mode, not a feature.

**Its GETs run no simulation and touch no COM.** A scoring request re-reads a
run that already finished. If no run has finished, the honest answer is an empty
report with a reason code, which ``reality_check`` already produces.

WHAT #51's LIVE RUNNER ADDED, and what it did NOT
--------------------------------------------------
:func:`start_parameter_search` is the first POST on this router. It still writes
nothing to any network: it ENQUEUES a job (``services.agent.search_job``) that
runs Vissim on CLONES, and the winner it eventually produces still has to go
through the same approval endpoint as everything else. Three properties are
worth stating because each of them is a way this route could have gone wrong:

* **The gate is re-derived here, from evidence this process read.** The request
  body carries selectors only — which scored run, which observation window,
  which behaviour, how much budget, which conversation. No ``ok``, no
  ``preconditions``, no ``force``, and nothing named on the body is treated as a
  verdict. A client that invents a passing gate is sending a field nothing reads.
* **A refusal is an ANSWER, not an HTTP error.** ``200`` with
  ``started: false`` and the named codes, exactly as ``reality_check`` returns
  its ``not_checkable`` reasons: those refusals are the product, and a UI should
  not have to translate a 4xx back into a sentence. A malformed body is still a
  422 — that is pydantic's job, not a refusal.
* **No COM runs in this handler.** The handler validates and returns; the job
  thread owns every Vissim call (CLAUDE.md: COM goes through the job queue,
  never a request handler).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services import calibration_rail, genesis_jobs, workspaces
from services.agent import mrwa_reference, saturation_flow, search_job, tools_network
from services.datasets import get_active_dataset, get_active_inpx_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


# ---------------------------------------------------------------------------
# Shared readers
# ---------------------------------------------------------------------------


def _shadow_model() -> dict[str, Any] | None:
    """The active dataset's offline parse, or ``None`` when there is no dataset.

    ``None`` rather than a 404: every caller here treats a missing network as
    MISSING EVIDENCE (which the rail reports honestly) rather than as an error,
    because a workspace with no upload yet is a normal state for this screen —
    it is the state the screen exists to describe.
    """
    try:
        path = Path(get_active_inpx_path())
    except Exception:  # noqa: BLE001 — no dataset configured at all
        return None
    if not path.is_file():
        return None
    try:
        return tools_network.build_shadow_model(path)
    except Exception as exc:  # noqa: BLE001 — an unparseable .inpx is evidence, not a 500
        logger.warning("calibration: shadow model unavailable (%s: %s)", type(exc).__name__, exc)
        return None


def _applied_rows(shadow: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """``verify_applied_row`` over every driving behaviour in the network.

    Returns ``None`` — not ``[]`` — when the network itself could not be read.
    The rail distinguishes the two: no network is "no evidence source", while
    a network with zero behaviours is a network whose behaviours are all
    unreadable, and they deserve different sentences.
    """
    if shadow is None:
        return None
    behaviors = shadow.get("driving_behaviors")
    if not isinstance(behaviors, list):
        return None
    rows: list[dict[str, Any]] = []
    for behavior in behaviors:
        if not isinstance(behavior, dict):
            continue
        rows.append(
            {
                "behavior_no": behavior.get("no"),
                "behavior_name": behavior.get("name"),
                "reference_count": behavior.get("reference_count"),
                "car_following_model": behavior.get("car_following_model"),
                # UNMODIFIED. #44 owns this verdict; this router only carries it.
                "verdict": saturation_flow.verify_applied_row(behavior),
            }
        )
    return rows


def _gate_for_active_dataset(language: str) -> dict[str, Any] | None:
    """The V1-V9 verdict for the generation that PUBLISHED the active dataset.

    Matched on ``publication.dataset_revision``, which
    ``genesis_jobs._publish_artifact`` records at publication time — never on
    "the newest job", which would attach some other generation's verdict to
    whatever network happens to be active. A consultant ``.inpx`` uploaded by
    hand matches nothing and correctly yields ``None``.
    """
    try:
        revision = str(get_active_dataset().get("revision") or "")
    except Exception:  # noqa: BLE001
        return None
    if not revision:
        return None
    try:
        jobs = genesis_jobs.list_jobs()
    except Exception as exc:  # noqa: BLE001
        logger.warning("calibration: genesis jobs unreadable (%s: %s)", type(exc).__name__, exc)
        return None
    for job in jobs:
        publication = job.get("publication") or {}
        if str(publication.get("dataset_revision") or "") != revision:
            continue
        try:
            return genesis_jobs.gate_payload(str(job.get("id") or ""), language=language)
        except Exception as exc:  # noqa: BLE001 — an ungated job is not an error here
            logger.info("calibration: gate unavailable for job (%s: %s)", type(exc).__name__, exc)
            return None
    return None


def _phase_diagram_index() -> list[dict[str, Any]] | None:
    """This workspace's 현시도 reviews, or ``None`` if the store cannot answer."""
    try:
        from routers.genesis import _phase_diagram_store

        return _phase_diagram_store().index_payload()
    except Exception as exc:  # noqa: BLE001
        logger.info("calibration: phase diagrams unavailable (%s: %s)", type(exc).__name__, exc)
        return None


def _health_report() -> dict[str, Any] | None:
    """The model inspection, run only when no generation gate answered.

    Deliberately lazy: it re-runs the whole constraint pass over the ``.inpx``,
    which is the most expensive thing on this page, and step 1 prefers the gate
    whenever one exists.
    """
    try:
        from routers.health import _runner

        path = Path(get_active_inpx_path())
        if not path.is_file():
            return None
        return _runner.run(str(path), dataset_meta=get_active_dataset())
    except Exception as exc:  # noqa: BLE001
        logger.warning("calibration: health check unavailable (%s: %s)", type(exc).__name__, exc)
        return None


def _language(request: Request) -> str:
    value = (request.headers.get("X-Language") or "").strip().lower()
    return value if value in {"ko", "en", "ja"} else "ko"


def _derived_targets(
    shadow: dict[str, Any] | None,
    *,
    period: str | None,
    observation_set_id: str | None,
) -> list[Any] | None:
    """The edit targets THIS server can derive, or ``None``.

    ONE derivation, called from all three of this router's reality-check call
    sites, because a page that shows the report, a rail that summarises it and a
    gate that is evaluated against it must not hold three opinions about which
    object a miss belongs to. The rules — and the dominance pick they refuse to
    make — live in ``services.agent.reality_check_targets``; this function is
    only the I/O half: it reads the observed workbook and hands it, with the
    offline ``.inpx`` parse the caller already had, to that pure derivation.

    ``None`` (never ``[]``) whenever nothing could be derived, and every such
    case leaves the report byte-identical to what it was before this unit:
    ``NO_TARGET_OBJECT`` refusals, naming what was missing.

    ``observation_set_id`` short-circuits, and it costs nothing: a stored
    observation set needs a ``site_nodes`` map, no route on this router accepts
    one, and ``observations.observed_side`` refuses without it
    (``SITE_NODE_MAP_MISSING``) — so those requests come back NOT_CHECKABLE with
    no nodes to target either way.

    Every failure degrades to ``None`` and a log line, the same rule the other
    gatherers on this router follow: an unreadable workbook is missing evidence
    for the targets, not a 500 for the whole screen.
    """
    if shadow is None or observation_set_id:
        return None
    try:
        # The MODULE, not a name bound at import: ``tools_metrics`` resolves the
        # active workbook through this same accessor, so one seam (a workspace
        # switch, or a test that repoints it) moves both and the derivation
        # cannot end up reading a different workbook than the report grades.
        from services import datasets, validation
        from services.agent import reality_check, reality_check_targets

        observed = reality_check.observed_nodes(
            validation.load_base_workbook(str(datasets.get_active_real_counts_excel())),
            period=period or None,
        )
        if observed["problem"] is not None:
            return None
        derived = reality_check_targets.derive_targets(
            observed=observed["nodes"], shadow_model=shadow
        )
    except Exception as exc:  # noqa: BLE001 — no target is a state, not a fault
        logger.warning(
            "calibration: edit targets could not be derived (%s: %s)", type(exc).__name__, exc
        )
        return None
    targets = derived["targets"]
    logger.debug(
        "calibration: derived %d edit target(s) over %d node(s)", len(targets), len(derived["nodes"])
    )
    return targets or None


def _reality_check(
    scenario_id: str | None,
    *,
    period: str | None,
    observation_set_id: str | None,
    observation_period: str | None,
    targets: list[Any] | None = None,
) -> dict[str, Any] | None:
    if not scenario_id:
        return None
    # Deferred like every other agent-tool import in the router layer: importing
    # tools_metrics at module scope pulls the whole tool registry in at app
    # startup and would reorder tool registration (see routers/assistant.py's
    # own note on the same import).
    from services.agent.tools_metrics import build_reality_check_report

    return build_reality_check_report(
        scenario_id,
        period=period or None,
        targets=targets or None,
        observation_set_id=observation_set_id or None,
        observation_period=observation_period or None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/reality-check")
def get_reality_check(
    scenario_id: str = Query(..., description="A finished run or project scenario to score"),
    period: str | None = Query(None, description="AM/PM window for the base counts workbook"),
    observation_set_id: str | None = Query(
        None, description="Score against a stored observation set instead of the base workbook"
    ),
    observation_period: str | None = Query(None, description="Window within the observation set"),
):
    """Score one run against the observed counts — the same report the agent gets.

    ``build_reality_check_report`` is called with the caller's arguments and its
    result is returned untouched, including its refusals: a run that does not
    exist, a multi-seed run whose volumes are not hour-comparable, an
    observation set with no rows. Those are ANSWERS with reason codes, not
    errors, so they come back 200 with ``status`` naming what happened rather
    than as an HTTP failure a UI would have to translate back into a sentence.

    ``targets`` are DERIVED here (:func:`_derived_targets`), which reverses what
    this docstring said until 2026-08-23. The old sentence — "the agent supplies
    them when a human asks it to act; a page that is only looking does not" —
    described a choice that was never available: no producer of targets existed
    on the server at all, so "not passing them" was not restraint, it was the
    reason every miss on this screen came back ``NO_TARGET_OBJECT`` forever. The
    derivation refuses wherever the join is not an identity, and the same one
    feeds ``GET /rail`` and the search gate so the three cannot disagree.

    ``node_control`` is still not passed, and that IS restraint: whether a node
    is signalised is a fact this router could read (``get_signal_heads``) but has
    not, and the diagnosis ladder says "unknown" rather than assuming.
    """
    shadow = _shadow_model()
    try:
        report = _reality_check(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
            targets=_derived_targets(
                shadow, period=period, observation_set_id=observation_set_id
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=409,
            detail=f"The run could not be scored. [{type(exc).__name__}: {exc}]",
        ) from exc
    if report is None:
        raise HTTPException(status_code=422, detail="scenario_id is required.")
    return report


@router.get("/applied-rows")
def get_applied_rows():
    """Does each driving behaviour sit on a published MRWA Table 5-6 row? (#44)

    ``verify_applied_row``'s verdict per behaviour, verbatim — including its
    ``why`` sentence, which is the one that names a PARTIALLY APPLIED set. The
    summary beside it counts the verdicts and nothing else.

    Read from the ``.inpx`` on disk, never from Vissim: this is the offline
    parse ``tools_network`` already caches by mtime.
    """
    shadow = _shadow_model()
    rows = _applied_rows(shadow)
    if rows is None:
        return {
            "available": False,
            "reason": "NO_READABLE_NETWORK",
            "behaviors": [],
            "summary": calibration_rail.summarize_applied_rows([]),
            "citation": mrwa_reference.TABLE_5_6_CITATION,
        }
    summary = calibration_rail.summarize_applied_rows(rows)
    return {
        "available": True,
        "reason": None,
        "behaviors": rows,
        "summary": {
            "readable": len(summary["readable"]),
            "on_published_row": len(summary["on_published_row"]),
            "off_published_row": len(summary["off_published_row"]),
            "guideline_standstill": len(summary["guideline_standstill"]),
            "default_coincident": len(summary["default_coincident"]),
            "unreadable": summary["unreadable"],
            "default_coincident_row_pcu": summary["default_coincident_row_pcu"],
        },
        "citation": saturation_flow.mrwa_reference.TABLE_5_6_CITATION,
    }


@router.get("/rail")
def get_flow_rail(
    request: Request,
    scenario_id: str | None = Query(None, description="Run to derive steps 10/11 from"),
    period: str | None = Query(None),
    observation_set_id: str | None = Query(None),
    observation_period: str | None = Query(None),
):
    """MRWA §2.12.1's thirteen steps, each with the status its evidence supports.

    The gathering order matters for cost, not for correctness: the generation
    gate is read first and the model inspection runs ONLY if no gate answered,
    because step 1 prefers the gate and the inspection is the expensive half.
    Every gather failure degrades to "no evidence" for the steps that read it —
    a rail that 500s because one artifact was unreadable would tell a modeller
    less than a rail that says which artifact is missing.

    ``sources`` reports which evidence bundles were actually found, so the UI
    can distinguish "the rail says no evidence" from "the rail was never given
    anything to read".
    """
    shadow = _shadow_model()
    gate = _gate_for_active_dataset(_language(request))
    health = None if gate else _health_report()
    try:
        report = _reality_check(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
            # The SAME derivation ``GET /reality-check`` uses. Step ⑩ counts
            # demand proposals and D4 entrance verdicts; a rail reading a
            # targetless report would have shown zero of both while the report
            # beside it showed them.
            targets=_derived_targets(
                shadow, period=period, observation_set_id=observation_set_id
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a broken run is missing evidence
        logger.warning("calibration: reality check unavailable (%s: %s)", type(exc).__name__, exc)
        report = None

    signals = shadow.get("signals") if isinstance(shadow, dict) else None
    pt_lines = shadow.get("pt_lines") if isinstance(shadow, dict) else None
    evidence = {
        "gate": gate,
        "health": health,
        "phase_diagrams": _phase_diagram_index(),
        "signal_controller_count": len(signals) if isinstance(signals, list) else None,
        "pt_line_count": len(pt_lines) if isinstance(pt_lines, list) else None,
        "applied_rows": _applied_rows(shadow),
        "reality_check": report,
        # #51's live runner: the newest search THIS workspace started, as the ⑫
        # track shows it. Gathered here rather than inside calibration_rail so
        # that module stays a pure function of an evidence bundle — the job
        # registry is per-process state, and a pure derivation must not reach
        # into it. ``None`` when this workspace has never started one.
        "parameter_search_job": search_job.job_card(search_job.latest_job()),
        # The behaviours a search could target, so the ⑫ track can offer the
        # CHOICE instead of the server picking one of several on the modeller's
        # behalf (see search_job.resolve_target_behaviour).
        "parameter_search_behaviours": search_job.searchable_behaviours(shadow),
    }
    rail = calibration_rail.derive_rail(evidence)
    rail["sources"] = {
        "gate": gate is not None,
        "health": health is not None,
        "phase_diagrams": evidence["phase_diagrams"] is not None,
        "shadow_model": shadow is not None,
        "reality_check": report is not None,
        "scenario_id": scenario_id or None,
    }
    return rail


class ParameterSearchStartRequest(BaseModel):
    """Selectors, and NOT ONE VERDICT.

    ``extra="forbid"`` is doing real work here rather than being tidy: it means a
    body carrying ``preconditions``, ``ok``, ``force`` or ``wired`` is a 422 at
    the door instead of a field that silently does nothing. The gate this route
    honours is re-derived from server-read evidence in
    ``search_job.evaluate_start``; there is no way to send one in.

    Every field is the same selector the READ side already takes, plus the two
    the job needs: which conversation the winner is remembered against, and how
    much wall clock it may spend.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(..., description="The scored run the gate is evaluated against")
    period: str | None = None
    observation_set_id: str | None = None
    observation_period: str | None = None
    #: Which driving behaviour to search. Optional only because a network with
    #: exactly one searchable behaviour has no choice to make; with several, the
    #: gate refuses by name rather than picking one (BEHAVIOUR_NOT_CHOSEN).
    behavior_no: str | None = None
    #: Wall-clock ceiling. Clamped to [MIN_BUDGET_S, MAX_BUDGET_S]; absent or
    #: unreadable means the card's own default.
    budget_s: float | None = None
    #: Simulated seconds per candidate run. Clamped the same way.
    duration_s: int | None = None
    #: The agent session the finished search is remembered against. Absent is a
    #: named refusal (NO_AGENT_SESSION), not a silent no-op: a search with
    #: nowhere to land is an hour of Vissim whose winner nothing can propose.
    session_id: str | None = None


@router.post("/parameter-search/start")
def start_parameter_search(request: Request, body: ParameterSearchStartRequest):
    """Start ONE range-bounded parameter search (#51), or refuse by name.

    The whole gate runs before anything is enqueued and it runs on evidence THIS
    process gathered — the same bundle :func:`get_flow_rail` builds, assembled
    here again rather than cached from the last rail read, because the run the
    caller names may have been re-scored since. ``search_job.evaluate_start``
    then collects every refusal (its own start refusals AND
    ``parameter_search.evaluate_preconditions``'s named gate refusals) instead of
    stopping at the first, so a modeller three doors from a search learns about
    all three now.

    Returns 200 either way. ``started`` says which happened; ``refusals`` carries
    the codes and the reader's-language sentences when it did not. See this
    module's docstring for why a refusal is not an HTTP error here.
    """
    # Only the generation gate is language-bound (it renders V1-V9 prose); every
    # refusal this route returns carries all three languages on the payload, so
    # the reader's language is picked on the client, not here.
    shadow = _shadow_model()
    gate = _gate_for_active_dataset(_language(request))
    health = None if gate else _health_report()
    # Derived ONCE and used twice below: the report the gate is evaluated
    # against, and the frozen scoring context every candidate is graded with.
    # A candidate scored against different targets than the gate saw would be
    # two opinions about the same network, one of them invisible.
    targets = _derived_targets(
        shadow, period=body.period, observation_set_id=body.observation_set_id
    )
    try:
        report = _reality_check(
            body.scenario_id,
            period=body.period,
            observation_set_id=body.observation_set_id,
            observation_period=body.observation_period,
            targets=targets,
        )
    except Exception as exc:  # noqa: BLE001 — an unscorable run is missing evidence
        logger.warning(
            "calibration: reality check unavailable for a search start (%s: %s)",
            type(exc).__name__,
            exc,
        )
        report = None

    applied = _applied_rows(shadow)
    signals = shadow.get("signals") if isinstance(shadow, dict) else None
    pt_lines = shadow.get("pt_lines") if isinstance(shadow, dict) else None
    # The rail's own step 6 is what the gate reads for SIGNAL_STEP_UNVERIFIED —
    # derived here from the same evidence the screen shows, so "the rail says
    # signals are done" and "the gate says signals are done" cannot disagree
    # between the page and the button on it.
    rail = calibration_rail.derive_rail(
        {
            "gate": gate,
            "health": health,
            "phase_diagrams": _phase_diagram_index(),
            "signal_controller_count": len(signals) if isinstance(signals, list) else None,
            "pt_line_count": len(pt_lines) if isinstance(pt_lines, list) else None,
            "applied_rows": applied,
            "reality_check": report,
        }
    )

    try:
        source_inpx = Path(get_active_inpx_path())
    except Exception:  # noqa: BLE001 — no dataset is a named refusal, not a 500
        source_inpx = None

    verdict = search_job.evaluate_start(
        reality_check=report,
        applied_rows=applied,
        rail=rail,
        shadow_model=shadow,
        source_inpx=source_inpx,
        workspace_id=workspaces.current_workspace_id(),
        session_id=body.session_id,
        behavior_no=body.behavior_no,
        budget_s=body.budget_s,
        duration_s=body.duration_s,
        scenario_id=body.scenario_id,
        period=body.period,
        observation_set_id=body.observation_set_id,
        observation_period=body.observation_period,
        targets=targets,
    )

    payload = {
        "started": False,
        "refusals": verdict["refusals"],
        "behaviours": verdict["behaviours"],
        "budget": verdict["budget"],
        "duration": verdict["duration"],
        "job": None,
    }
    if not verdict["ok"] or verdict["plan"] is None:
        return payload

    record = search_job.start(verdict["plan"])
    if record is None:
        # Lost the atomic claim to a POST that arrived at the same moment. The
        # SAME named refusal ``evaluate_start`` would have produced a moment
        # earlier, from the same builder — a race must not be a different answer
        # than the check.
        return {**payload, "refusals": [search_job.already_running_refusal()]}
    if record.get("status") == search_job.FAILED:
        # Claimed, but its thread never started (thread exhaustion). The record
        # is already terminal, so nothing is wedged — and ``started: true`` here
        # would be the worse half of that failure: a screen reporting a running
        # search over a thread that does not exist.
        return {
            **payload,
            "refusals": [search_job.job_not_started_refusal(record.get("error"))],
            "job": search_job.job_card(record),
        }
    return {**payload, "started": True, "job": search_job.job_card(record)}


@router.get("/parameter-search/jobs/{job_id}")
def get_parameter_search_job(job_id: str):
    """One search job, as the ⑫ track shows it — or 404.

    Workspace-scoped by the same rule as everything else here: a job belonging to
    another project must be indistinguishable from a job that does not exist, so
    the ownership check comes BEFORE the existence answer.
    """
    record = search_job.get_job(job_id)
    if record is None or str(record.get("workspace_id") or "") != workspaces.current_workspace_id():
        raise HTTPException(status_code=404, detail="No such parameter-search job.")
    return search_job.job_card(record)


@router.get("/pending-approvals")
def list_pending_approvals(request: Request):
    """Every agent pause in THIS workspace that is still waiting for a human.

    Read-only in the strict sense: the pending file is a SINGLE-USE token and
    this route does not spend it. ``POST /api/assistant/agent/approve`` remains
    the only thing that deletes it, and remains the only endpoint that can act
    on it — this route exists so the pause is visible on the calibration screen
    instead of only inside the tab that happened to open the stream.

    Ownership is the same fail-closed predicate the assistant router uses
    (``sessions.session_workspace_matches``, via that router's own wrapper) —
    imported rather than re-derived, because a second implementation of "may
    this workspace read this session" is exactly the divergence that turned
    into a measured fail-open hole once already (see its docstring).

    ``car_following_set`` is rebuilt from the in-process broker, so a pause that
    outlived a backend restart comes back without its Table 5-6 panel and with
    ``car_following_set: null``. That is honest: the grant it describes no
    longer exists either, and inventing the panel from the file would show a
    human a set the approve leg could not re-mint. ``searched_search`` (#51-c) is
    the same field for the second grant species, rebuilt from
    ``searched_behaviour_broker`` on exactly the same terms.
    """
    # These four are ``routers.assistant``'s own helpers. Imported rather than
    # copied on purpose: the pending-approval file's path, its parse, the
    # workspace ownership check and the card payload are all boundary rules
    # that must have exactly one implementation (CLAUDE.md; that module's own
    # note on why _agent_session_work_dir stopped being duplicated).
    from routers.assistant import (
        _agent_session_work_dir,
        _car_following_card_payload,
        _read_pending_approval,
        _searched_search_card_payload,
        _session_belongs_to_workspace,
    )

    workspace_id = workspaces.current_workspace_id()
    # The sessions root, asked of the FUNCTION rather than of ``SESSIONS_ROOT``
    # — the id is a throwaway because only ``.parent`` is used, and going
    # through the function means a test that patches the assistant router's
    # sessions seam patches this listing too (that router does the same thing
    # in ``_session_workspace_matches`` for the same stated reason).
    root = _agent_session_work_dir("0" * 12).parent
    pending: list[dict[str, Any]] = []
    if not root.is_dir():
        return {"pending": [], "workspace_id": workspace_id}

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        session_id = child.name
        if not _session_belongs_to_workspace(session_id, workspace_id):
            continue
        record = _read_pending_approval(_agent_session_work_dir(session_id))
        if not record:
            continue
        entry = {
            "session_id": session_id,
            "approval_id": record.get("approval_id"),
            "created_at": record.get("created_at"),
            "message": record.get("message"),
            "language": record.get("language"),
            "actions": record.get("actions") or [],
            "warnings": record.get("warnings") or [],
            # Always empty today, exactly as the stream's own payload documents
            # — carried so this route and the NDJSON event have one shape.
            "assumptions": [],
            "focus": record.get("focus"),
            "car_following_set": None,
            "searched_search": None,
        }
        set_id = record.get("car_following_set_id")
        if set_id:
            try:
                entry["car_following_set"] = _car_following_card_payload(
                    str(set_id), session_id, _shadow_model()
                )
            except Exception as exc:  # noqa: BLE001 — a lost broker is not an error
                logger.info(
                    "calibration: car-following card unavailable (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
        search_id = record.get("searched_search_id")
        if search_id:
            try:
                entry["searched_search"] = _searched_search_card_payload(
                    str(search_id), session_id, _shadow_model()
                )
            except Exception as exc:  # noqa: BLE001 — a lost broker is not an error
                logger.info(
                    "calibration: searched-parameter card unavailable (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
        pending.append(entry)

    pending.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    return {"pending": pending, "workspace_id": workspace_id}

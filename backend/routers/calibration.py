"""``/api/calibration`` — the calibration workspace's READ side. Unit #50.

Every route here is a GET, every one of them is pure with respect to this
product's state, and not one of them decides anything about a network. They
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

**It runs no simulation and touches no COM.** A scoring request re-reads a run
that already finished. If no run has finished, the honest answer is an empty
report with a reason code, which ``reality_check`` already produces.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from services import calibration_rail, genesis_jobs, workspaces
from services.agent import mrwa_reference, saturation_flow, tools_network
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


def _reality_check(
    scenario_id: str | None,
    *,
    period: str | None,
    observation_set_id: str | None,
    observation_period: str | None,
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

    Nothing is passed for ``targets``/``node_control``: this is the SCORING
    call, and both of those are the caller-supplied inputs that turn a score
    into a proposal about a specific vehicle input. The agent supplies them
    when a human asks it to act; a page that is only looking does not.
    """
    try:
        report = _reality_check(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
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

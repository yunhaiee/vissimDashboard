"""``/api/calibration`` — the calibration workspace's read side (#50), the search
control added 2026-08-21 (``POST /parameter-search/start``, #51), and the signed
human attestation added 2026-08-23 (``POST /step-attestations`` and its
revocation, #53).

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
**It adds no second approval machine** — and, until #51, no write path at all.
Approving a machine's proposed EDIT is still not something this router can do:
the calibration screen approves through the endpoint that already exists,
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

WHAT #53's SIGNED ATTESTATION ADDED
------------------------------------
Two more POSTs, and they are the first ones on this router that write something
a human said rather than something a machine will do:
``POST /step-attestations`` records ONE named person's claim that a §2.12.1 step
is done, and ``POST /step-attestations/{id}/revoke`` withdraws it. Three
properties, each of them a way this could have gone wrong:

* **Neither route can decide a status.** They write to
  ``services.calibration_attestation``; ``GET /rail`` re-derives every step from
  that store on the next read. The POST responses carry the RECORD and no status
  word at all, so there is exactly one place a step's state is decided.
* **The attester is the session and the clock is the server's** — the request
  models forbid extra fields, so a body carrying ``attested_by`` is a 422 rather
  than somebody else's name against a step. Same rule as
  ``routers/genesis.confirm_phase_diagram``, which is the act this is modelled
  on.
* **A signature never becomes ``DONE``.** ``CONFIRMED_BY_PERSON`` is its own
  word, it upgrades only ``NO_EVIDENCE``, and it expires when the ``.inpx`` it
  was signed against changes. That ranking lives in ``calibration_rail``, not
  here.

WHAT #54's NAMED DEMAND TARGET ADDED
------------------------------------
Two more POSTs and one GET, and they answer a question this router had been
ASKING a modeller with no way for them to reply. ``reality_check_targets``
refuses to pick between several vehicle inputs feeding one node — on the real
client model node 8 has six — so the report came back ``NO_TARGET_OBJECT``, the
ladder could not establish whether that entrance was measured, and the node read
분류 불가 forever. ``POST /demand-targets`` records a named person's CHOICE among
those candidates, ``GET /demand-targets/candidates`` is the list they choose
from, and ``POST /demand-targets/{id}/revoke`` withdraws it.

* **The candidate list is the server's, on both legs.** The picker renders
  ``_derive_all``'s own per-node output, and the POST re-derives it here to check
  the choice against — never against a list the request asserted. A client
  cannot offer, or claim, a candidate the derivation does not vouch for.
* **The namer is the session and the clock is the server's**, the same door
  ``StepAttestationRequest`` closes, for the same reason.
* **Naming does not decide a classification.** These routes return the RECORD.
  ``GET /reality-check`` and ``GET /rail`` re-derive the node from the store on
  the next read, and the mapping is threaded through ``_derived_targets`` — the
  one seam all three reality-check call sites and the search gate share — so a
  named input cannot be visible on one of those surfaces and absent from
  another.
* **A named mapping does not make the assumption true.** The target it produces
  still carries ``fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL``, now beside the name of
  the person who made it.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.auth import current_user
from services import (
    calibration_attestation,
    calibration_rail,
    demand_target_map,
    genesis_jobs,
    workspaces,
)
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


def _step_attestation_index() -> dict[int, dict[str, Any]] | None:
    """This workspace's signed step attestations, or ``None`` if unreadable (#53).

    Same shape and the same failure rule as :func:`_phase_diagram_index`: a
    deferred import, and any failure degrades to ``None`` — which the rail reads
    as "no signature", i.e. exactly the answer this screen gave before #53. The
    other direction (an unreadable store inventing a signature) is the one that
    must not exist, and this cannot produce it.
    """
    try:
        return calibration_attestation.index_payload()
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "calibration: step attestations unavailable (%s: %s)", type(exc).__name__, exc
        )
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


def _demand_target_index() -> dict[str, dict[str, Any]] | None:
    """This workspace's human-named demand targets, or ``None`` if unreadable (#54).

    FRESH and UNREVOKED only — the same shape and the same failure rule as
    :func:`_step_attestation_index`: a deferred import, and any failure degrades
    to ``None``, which the derivation reads as "nobody named anything", i.e.
    exactly the answer this screen gave before #54. The derivation re-checks
    both flags itself; this filter is the first of the two guards, not the only
    one.
    """
    try:
        return demand_target_map.fresh_index_payload()
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "calibration: demand-target mappings unavailable (%s: %s)", type(exc).__name__, exc
        )
        return None


def _derive_all(
    shadow: dict[str, Any] | None,
    *,
    period: str | None,
    observation_set_id: str | None,
    human_mappings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """``reality_check_targets.derive_targets``'s WHOLE answer, or ``None``.

    ONE derivation, called from all three of this router's reality-check call
    sites AND from the demand-target routes, because a page that shows the
    report, a rail that summarises it, a gate that is evaluated against it and a
    picker that offers a modeller a choice must not hold four opinions about
    which object a miss belongs to. The rules — and the dominance pick they
    refuse to make — live in ``services.agent.reality_check_targets``; this
    function is only the I/O half: it reads the observed workbook and hands it,
    with the offline ``.inpx`` parse the caller already had, to that pure
    derivation.

    ``observation_set_id`` short-circuits, and it costs nothing: a stored
    observation set needs a ``site_nodes`` map, no route on this router accepts
    one, and ``observations.observed_side`` refuses without it
    (``SITE_NODE_MAP_MISSING``) — so those requests come back NOT_CHECKABLE with
    no nodes to target either way.

    Every failure degrades to ``None`` and a log line, the same rule the other
    gatherers on this router follow: an unreadable workbook is missing evidence
    for the targets, not a 500 for the whole screen.

    A ONE-LINE FORWARD since #56 (c): the body used to live here. It moved to
    ``services.agent.reality_check_targets.derive_targets_for_active_dataset``
    so an agent TOOL (``services/agent/tools_calibration.py``'s
    ``get_demand_target_derivation``, which must not import ``routers.*``)
    could reach the identical derivation instead of forking a second one that
    re-reads the same workbook. Kept here, under this name, because this
    module's own tests call ``_derive_all`` directly.
    """
    from services.agent import reality_check_targets

    return reality_check_targets.derive_targets_for_active_dataset(
        shadow_model=shadow,
        period=period,
        observation_set_id=observation_set_id,
        human_mappings=human_mappings,
    )


def _derived_targets(
    shadow: dict[str, Any] | None,
    *,
    period: str | None,
    observation_set_id: str | None,
) -> list[Any] | None:
    """The edit targets THIS server can derive, or ``None``.

    ``None`` (never ``[]``) whenever nothing could be derived, and every such
    case leaves the report byte-identical to what it was before the targets unit
    landed: ``NO_TARGET_OBJECT`` refusals, naming what was missing.

    #54: the human-named mappings are gathered HERE rather than by each caller,
    for the reason this function exists at all — the report, the rail and the
    search gate must be derived from the same account of the network, and a
    mapping visible to one of them and not the others is precisely the
    two-opinions failure the targets unit's own tests were written against.

    A thin slice of :func:`_derived_targets_and_reasons` since #56 (b), which
    added a SECOND thing this router reads out of the same derivation (why a
    target-less node has no target). This function's own signature and return
    value are unchanged -- kept exactly as they were for the callers (the
    search-start gate among them) and the tests that pin this name directly.
    """
    return _derived_targets_and_reasons(
        shadow, period=period, observation_set_id=observation_set_id
    )[0]


def _derived_targets_and_reasons(
    shadow: dict[str, Any] | None,
    *,
    period: str | None,
    observation_set_id: str | None,
) -> tuple[list[Any] | None, dict[str, dict[str, Any]] | None]:
    """``(targets, demand_target_reasons)`` from ONE derivation call — #56 (b).

    ``demand_target_reasons`` is ``{node_id: {"reason": ..., "candidate_count":
    ...}}`` for every node the derivation looked at, read off the SAME
    ``derived["nodes"]`` audit ``_demand_target_payload`` already reads for the
    picker. It exists so the diagnosis ladder's target-less rung
    (``geh_ladder.classify_node``) can tell "no vehicle input reaches this
    node's approaches" (``NO_VEHICLE_INPUT_ON_APPROACH``, 0 candidates) apart
    from "several do and this product will not pick one"
    (``MULTIPLE_VEHICLE_INPUT_CANDIDATES``, 2+) — the two reasons a target
    never resolved, which DECISIONS.md's #56 (b) measurement found the old
    next-action sentence conflating into one instruction that named an input
    even on the 0-candidate node it cannot possibly help.

    Calling ``_derive_all`` a SECOND time here (rather than threading a return
    value through ``_derived_targets``) would read the observed-counts workbook
    twice per request for no reason this module's own doctrine allows — see
    that function's docstring on "must not hold four opinions" — so this is
    the ONE call, and ``_derived_targets`` above is now defined in terms of it.
    """
    derived = _derive_all(
        shadow,
        period=period,
        observation_set_id=observation_set_id,
        human_mappings=_demand_target_index(),
    )
    if derived is None:
        return None, None
    targets = derived["targets"]
    logger.debug(
        "calibration: derived %d edit target(s) over %d node(s)", len(targets), len(derived["nodes"])
    )
    reasons: dict[str, dict[str, Any]] = {}
    for node_id, why in (derived.get("nodes") or {}).items():
        demand = (why or {}).get("vehicle_input") or {}
        reasons[str(node_id)] = {
            "reason": demand.get("reason"),
            "candidate_count": len(demand.get("candidate_inputs") or []),
            # #59: what the modeller is actually asked to choose between. The
            # ladder's "several inputs feed this node" sentence would otherwise
            # keep saying six where the picker now offers two, which is the
            # instruction naming a choice the screen does not present.
            "candidate_group_count": len(demand.get("candidate_groups") or []),
        }
    return targets or None, reasons


def _reality_check(
    scenario_id: str | None,
    *,
    period: str | None,
    observation_set_id: str | None,
    observation_period: str | None,
    targets: list[Any] | None = None,
    demand_target_reasons: dict[str, dict[str, Any]] | None = None,
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
        demand_target_reasons=demand_target_reasons or None,
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
        # #56 (b): the SAME derivation call as before, now also carrying WHY a
        # target-less node has no target (0 candidates vs several) so the
        # ladder's next-action sentence can branch instead of telling a
        # 0-candidate node to name an input it does not have.
        targets, demand_target_reasons = _derived_targets_and_reasons(
            shadow, period=period, observation_set_id=observation_set_id
        )
        report = _reality_check(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
            targets=targets,
            demand_target_reasons=demand_target_reasons,
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
        # The SAME derivation ``GET /reality-check`` uses. Step ⑩ counts
        # demand proposals and D4 entrance verdicts; a rail reading a
        # targetless report would have shown zero of both while the report
        # beside it showed them. #56 (b): also the SAME per-node demand-target
        # reasons, so a rail-driven card branches its next action exactly like
        # the report's own card does.
        targets, demand_target_reasons = _derived_targets_and_reasons(
            shadow, period=period, observation_set_id=observation_set_id
        )
        report = _reality_check(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
            targets=targets,
            demand_target_reasons=demand_target_reasons,
        )
    except Exception as exc:  # noqa: BLE001 — a broken run is missing evidence
        logger.warning("calibration: reality check unavailable (%s: %s)", type(exc).__name__, exc)
        report = None

    signals = shadow.get("signals") if isinstance(shadow, dict) else None
    pt_lines = shadow.get("pt_lines") if isinstance(shadow, dict) else None
    attestations = _step_attestation_index()
    evidence = {
        "gate": gate,
        "health": health,
        "phase_diagrams": _phase_diagram_index(),
        # #53. The nine signable steps' human attestations, gathered here for
        # the same reason the search job is: ``calibration_rail`` stays a pure
        # function of a bundle and never reaches into a registry.
        "step_attestations": attestations,
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
        # Whether the SIGNATURE STORE could be read at all — distinct from "it
        # holds nothing", which is the normal state and reads as no evidence.
        "step_attestations": attestations is not None,
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
            # Gathered here too (#53) so this rail and the screen's rail cannot
            # describe the same nine steps differently. The gate itself reads
            # only step 6, which is not signable — but a rail built from a
            # SMALLER bundle than the one on screen is exactly how two opinions
            # about one project start.
            "step_attestations": _step_attestation_index(),
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


# ---------------------------------------------------------------------------
# #53 — the signed human attestation, the one write this screen owns
# ---------------------------------------------------------------------------


class StepAttestationRequest(BaseModel):
    """Which step, and why. NOT who, and NOT when.

    ``extra="forbid"`` is load-bearing exactly as it is on
    :class:`ParameterSearchStartRequest`: a body carrying ``attested_by`` or
    ``attested_at`` is a 422 AT THE DOOR rather than a field that quietly does
    nothing, so "the attester is the session and the clock is the server's" is a
    property a caller can verify rather than a promise. Same rule, same reason,
    as ``routers/genesis.ConfirmPhaseDiagramRequest``.
    """

    model_config = ConfigDict(extra="forbid")

    step_number: int = Field(..., description="One of the nine signable §2.12.1 steps")
    reason: str = Field(
        ...,
        description=(
            "Why this person is signing. Mandatory and non-empty after stripping — a "
            "signature with no stated reason is a tick."
        ),
    )


class RevokeStepAttestationRequest(BaseModel):
    """Why the signature is being withdrawn. Mandatory, for the same reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., description="Why this attestation no longer holds")


@router.post("/step-attestations")
def create_step_attestation(
    body: StepAttestationRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Record ONE human's signature on one calibration step. The load-bearing act.

    The attester is the signed-in user and the timestamp is the server's; the
    ``.inpx`` hash the claim is bound to is read off disk here, not sent. So the
    only things a caller decides are WHICH step and WHY — everything that makes
    the record accountable comes from the server side of the request.

    ``current_user``, not ``require_approver``: this is the 현시도 analogue
    (``POST /api/genesis/phase-diagrams/{id}/confirmation``), where any named
    modeller may attest what they did. Approving an EDIT the machine proposed is
    a different act and still goes through ``POST /api/assistant/agent/approve``
    with its reviewer check.

    WHAT THIS RESPONSE DOES NOT CONTAIN: a status. The rail re-derives every
    step from the store on the next read, so this returns the record it wrote
    and the client re-reads ``GET /rail`` — a status word on this response would
    be a second place a step's state is decided.
    """
    try:
        return calibration_attestation.record_attestation(
            step_number=body.step_number,
            reason=body.reason,
            attested_by=str(user.get("username") or user.get("id") or "").strip(),
        )
    except calibration_attestation.StepAttestationRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except calibration_attestation.RegistryUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The signature could not be written because this project's existing ones could "
                f"not be read. Nothing was recorded. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The signature could not be written. Nothing was recorded. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


@router.post("/step-attestations/{attestation_id}/revoke")
def revoke_step_attestation(
    attestation_id: str,
    body: RevokeStepAttestationRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Withdraw one signature, with a reason. Nothing is deleted.

    ANY signed-in user may revoke, and the store records who did and why beside
    the original signing event (see ``calibration_attestation``'s own note on
    why the two acts are deliberately asymmetric). The step then falls back to
    whatever its evidence says on the next rail read, which for a manual-check
    area is the sentence it always had.
    """
    try:
        return calibration_attestation.revoke_attestation(
            attestation_id,
            revoked_by=str(user.get("username") or user.get("id") or "").strip(),
            reason=body.reason,
        )
    except calibration_attestation.StepAttestationRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except calibration_attestation.RegistryUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The revocation could not be written because this project's signatures could not "
                f"be read. Nothing was changed. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The revocation could not be written. Nothing was changed. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


# ---------------------------------------------------------------------------
# #54 — the human-named demand target: the choice a refusal was asking for
# ---------------------------------------------------------------------------


class DemandTargetRequest(BaseModel):
    """Which node, which input, which link, and WHY. NOT who, and NOT when.

    ``extra="forbid"`` is load-bearing exactly as it is on
    :class:`StepAttestationRequest`: a body carrying ``named_by`` or ``named_at``
    is a 422 AT THE DOOR rather than a field that quietly does nothing, so "the
    namer is the session and the clock is the server's" is a property a caller
    can verify rather than a promise.

    The LINK is on the body and is not derived from the input number, even
    though a vehicle input has exactly one link: the modeller is asserting a
    triple, and the server checks all three of it against the candidate list.
    Deriving the link here would mean the thing being checked was partly
    supplied by the checker.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., description="The counts-workbook node this input is said to drive")
    vehicle_input_no: str | None = Field(
        None, description="One of that node's candidate vehicle inputs (the #54 shape)"
    )
    #: Unit #59's shape: the vehicle-class inputs of ONE candidate link-group.
    #: The full membership is asserted by the caller and checked for SET
    #: EQUALITY against the server's own groups, for the reason the link is
    #: asserted rather than derived (above): the reviewer approves the classes
    #: they SAW, and a group that gained one since must be refused rather than
    #: quietly enrolled. Exactly one of this and ``vehicle_input_no`` is filled;
    #: both is a named refusal, not a precedence rule.
    vehicle_input_nos: list[str] | None = Field(
        None, description="The member vehicle inputs of one candidate link-group"
    )
    link_no: str = Field(..., description="The link that input feeds — checked, never assumed")
    reason: str = Field(
        ...,
        description=(
            "Why this person says this input drives this node's total. Mandatory and non-empty "
            "after stripping — a mapping with no stated reason is a tick."
        ),
    )
    #: The counts window the candidate list is computed against, so the server
    #: checks the choice against the SAME candidates the picker offered.
    period: str | None = None


class RevokeDemandTargetRequest(BaseModel):
    """Why the mapping is being withdrawn. Mandatory, for the same reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., description="Why this mapping no longer holds")


def _demand_target_payload(period: str | None) -> dict[str, Any]:
    """Per node: its candidate inputs, its derivation reason, and any mapping.

    Everything here is the SERVER's: the candidates are
    ``reality_check_targets``' own per-node list (the same one the store checks a
    choice against), the reason is its own code, and the mapping is the store's
    own summary including ``stale``. The picker renders this and computes
    nothing — a client that assembled its own candidate list could offer a
    choice the store would then refuse, which is the two-opinions failure in its
    most user-hostile form.

    A STALE mapping is included, carrying its flag, because the screen has to
    show the claim it is asking to have re-made. The derivation ignores it; this
    payload is not the derivation.

    WHY THIS DERIVES OVER THE FULL INDEX AND ``_derived_targets`` OVER THE FRESH
    ONE, and why that is not two opinions: the derivation drops a stale mapping
    ITSELF (its second guard), so ``reason`` and ``chosen`` come out byte-
    identical either way. What the extra rows buy is the ``human_mapping_ignored``
    CODE — "your mapping is stale", "the input you named is no longer a
    candidate" — which is the one thing a modeller looking at a node they
    already answered needs to be told, and which a fresh-only index can never
    produce because a stale mapping is not in it.
    """
    standing: dict[str, dict[str, Any]] = {}
    try:
        standing = demand_target_map.index_payload()
    except Exception as exc:  # noqa: BLE001 — an unreadable store is a state
        logger.info(
            "calibration: demand-target mappings unreadable (%s: %s)", type(exc).__name__, exc
        )
        standing = {}
    derived = _derive_all(
        _shadow_model(), period=period, observation_set_id=None, human_mappings=standing
    )

    if derived is None:
        return {
            "available": False,
            "reason": "NO_DERIVATION",
            "nodes": {},
            # The mappings survive a derivation this server could not run: a
            # modeller must still be able to SEE and REVOKE what they named.
            "mappings": standing,
        }

    nodes: dict[str, Any] = {}
    for node_id, why in (derived.get("nodes") or {}).items():
        demand = (why or {}).get("vehicle_input") or {}
        nodes[str(node_id)] = {
            "approach_links": list((why or {}).get("approach_links") or []),
            "reason": demand.get("reason"),
            "chosen": demand.get("chosen"),
            "chosen_group": demand.get("chosen_group"),
            "candidates": list(demand.get("candidate_inputs") or []),
            # #59: what the picker actually offers. The raw inputs stay beside
            # it — a reader has to be able to see WHICH classes a group holds,
            # and the store still checks a #54 single-input naming against them.
            "candidate_groups": list(demand.get("candidate_groups") or []),
            # The grouping and the class rule for a node that RESOLVED to a
            # group, so "why does this node have a target now when it refused
            # before" is answered on the same payload that answers "which".
            "group": demand.get("group"),
            "class_rule": demand.get("class_rule"),
            "human_mapping_ignored": demand.get("human_mapping_ignored"),
            "mapping": standing.get(str(node_id)),
        }
    return {"available": True, "reason": None, "nodes": nodes, "mappings": standing}


@router.get("/demand-targets/candidates")
def get_demand_target_candidates(
    period: str | None = Query(None, description="AM/PM window for the base counts workbook"),
):
    """Which vehicle inputs a modeller may name for each node, and what stands.

    Read-only, and the same derivation everything else on this router reads. It
    exists because a refusal that says "several inputs are candidates and this
    product will not pick between them" is only half an answer until the screen
    can show WHICH several.
    """
    return _demand_target_payload(period)


@router.post("/demand-targets")
def create_demand_target(
    body: DemandTargetRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Record ONE human's mapping of a vehicle input to a node's demand.

    The namer is the signed-in user, the timestamp is the server's, and the
    ``.inpx`` hash the claim is bound to is read off disk here, not sent. The
    CANDIDATE LIST the choice is checked against is re-derived here too, from
    the network and the workbook this process read — a caller cannot send one,
    and the check is therefore against the same list the picker was offered
    rather than against a list the request asserted.

    ``current_user``, not ``require_approver``: this is the #53 analogue. Naming
    what YOU know about a network is not approving what the MACHINE proposed;
    the latter is still ``POST /api/assistant/agent/approve``.

    WHAT THIS RESPONSE DOES NOT CONTAIN: a status, a diagnosis, or a rung. The
    report and the rail re-derive everything from the store on the next read, so
    there is exactly one place a node's classification is decided.
    """
    # The mappings are threaded here too, and it is CONSISTENCY rather than
    # necessity: a candidate list is mapping-independent by construction (it is
    # the inputs feeding the node's counted approach links, which no signature
    # moves), so this call would answer the same either way today. Deriving one
    # of this router's four sites from a smaller bundle than the rest is how the
    # POST's validation list would eventually drift from the picker's, and
    # ``test_the_candidate_list_is_the_same_with_and_without_mappings`` pins the
    # invariant so a refactor cannot make the difference matter silently.
    derived = _derive_all(
        _shadow_model(),
        period=body.period,
        observation_set_id=None,
        human_mappings=_demand_target_index(),
    )
    node_why = ((derived or {}).get("nodes") or {}).get(str(body.node_id).strip())
    demand_why = (node_why or {}).get("vehicle_input") or {}
    candidates = None if node_why is None else list(demand_why.get("candidate_inputs") or [])
    # #59: the GROUPS come from the same derivation call and the same node, so
    # the list a group naming is checked against is the list the picker offered.
    # ``None`` when the node was never derived, which the store turns into its
    # own named refusal rather than treating as "no constraints".
    groups = None if node_why is None else list(demand_why.get("candidate_groups") or [])
    try:
        return demand_target_map.record_mapping(
            node_id=body.node_id,
            vehicle_input_no=body.vehicle_input_no,
            vehicle_input_nos=body.vehicle_input_nos,
            link_no=body.link_no,
            reason=body.reason,
            named_by=str(user.get("username") or user.get("id") or "").strip(),
            candidates=candidates,
            groups=groups,
        )
    except demand_target_map.DemandTargetRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except demand_target_map.RegistryUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The mapping could not be written because this project's existing ones could not "
                f"be read. Nothing was recorded. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The mapping could not be written. Nothing was recorded. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


@router.post("/demand-targets/{mapping_id}/revoke")
def revoke_demand_target(
    mapping_id: str,
    body: RevokeDemandTargetRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Withdraw one mapping, with a reason. Nothing is deleted.

    ANY signed-in user may revoke, and the store records who did and why beside
    the original naming event. The node then falls back to whatever the network
    itself supports on the next read — which, for a node with six candidates, is
    the honest refusal it had before anybody named anything.
    """
    try:
        return demand_target_map.revoke_mapping(
            mapping_id,
            revoked_by=str(user.get("username") or user.get("id") or "").strip(),
            reason=body.reason,
        )
    except demand_target_map.DemandTargetRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except demand_target_map.RegistryUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The revocation could not be written because this project's mappings could not be "
                f"read. Nothing was changed. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The revocation could not be written. Nothing was changed. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


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

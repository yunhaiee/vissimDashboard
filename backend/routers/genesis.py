"""``/api/genesis`` — the browser's door to the OSM → draft → emit → gate chain.

Roadmap v2 §5.B row **B1**. Until this router the whole chain lived in
``backend/tools/*.py`` and a consultant had no button; ``services/genesis_jobs.py``
owns the work and the A4 rule (the gate is the exit door), and this file is the
HTTP boundary over it and nothing more.

**Two-step upload, for the reason ``routers/observations.py`` states.** This
backend has no ``python-multipart`` dependency and ``requirements.txt`` is not
this unit's to change, so a ``multipart/form-data`` handler would 500 on the
first request. ``POST /uploads`` therefore takes the raw ``.osm`` bytes as the
body with the name in ``X-Filename`` (or ``?filename=``) and returns a token;
``POST /jobs`` names that token. Both the 413 guard and its byte-level second
check are IMPORTED from ``routers/datasets.py`` — the same numbers, not a copy
that can drift.

**Side inputs — Track G's caller wiring.** ``POST /side-inputs`` stages the DATA
the optional layers need (``?kind=demand`` → counts + the site→leg→link join;
``?kind=signals`` → controllers + the approach→phase mapping), and ``POST /jobs``
names those tokens beside the ``.osm``'s. It is the ``.osm``'s own door one tree
over — authenticated, capped, validated before a token exists — because a
generation must have exactly one kind of entrance. Nothing enters by a
filesystem path in a job payload, and the job composes only the four-layer shape
``services/genesis/compose.py`` extracted from the build a real Vissim opened.

**``/demand-inputs/*`` — the demand side input's own clickable path.** Before
this pair, building a ``demand`` side input meant hand-writing the
``genesis.demand.v1`` JSON yourself. ``POST /demand-inputs/parse`` takes ONE
Daejeon counts workbook, runs it through ``DaejeonCountAdapter`` and stages the
result under its own ``counts`` kind — a token meaningful to exactly one other
call. ``POST /demand-inputs/build`` takes that token plus the
``approach_links`` join a human types in a browser table, checks it against
the counts with the SAME function ``services.genesis.demand.generate_demand``
itself calls (``check_approach_link_sites`` — an unknown site or leg refuses
here, with that module's own reason, rather than three steps later inside a
job), and stages the assembled document through the SAME
``genesis_jobs.stage_side_input`` / ``side_inputs.parse_side_input`` the raw
``POST /side-inputs`` route uses. No second door: this pair only ever produces
what that route already accepts. ``GET /side-inputs`` lists every staged
document (kind, filename, summary) so the job-start form can offer pickers
instead of a browser-typed token.

**Auth.** Every MUTATING route takes ``Depends(current_user)``: a generation
replaces the project's active dataset, and "who built this network" has to be
answerable in the audit document that ships with it. Reads stay open, in parity
with ``/api/simulations`` — reading the record of a decision is not making one
(``tests/test_auth_core.py``'s scope guard carries this ledger). The
authenticated user's username is stamped on the job at submission.

**Workspace.** Nothing here binds a workspace: ``main.WorkspaceScopeMiddleware``
already resolved ``X-Workspace-Id`` before routing, 404s an unknown one and
403s a non-default one this caller may not enter. These routes are therefore
NOT in ``main._MEMBERSHIP_EXEMPT_PREFIXES`` and must never be — a genesis job
writes into a project's tree and publishes into its dataset slot.

**No COM.** Generation is pure Python; nothing here imports ``vissim_com`` and
no request handler ever would (``docs/phase2/00-EXECUTION-GUIDE.md`` §1). That
is exactly why a fresh generation publishes as ``provisional``: V1 needs a real
``LoadNet``, and it arrives later through ``POST /jobs/{id}/live-check``.

**``/region/*`` — roadmap v2 §5.B row B2.** The four routes at the bottom of
this file put the .osm's ORIGIN inside the product: a region in,
``services/osm_fetch.py`` asks Overpass for it, and the answer is staged through
``genesis_jobs.stage_osm`` — the same function ``POST /uploads`` calls, handing
back the same token. So a fetched region enters generation on the road B1
already proved; there is deliberately no second way into ``gate_and_publish``.

Two of them are pure arithmetic and stay open (``/region/limits``,
``/region/preview``): they measure a box against the projection's own validity
radius so a browser can show the area against the cap BEFORE anything is
downloaded, and no network call happens in either. ``POST /region/fetches``
takes ``current_user`` and answers 202 — it is the one path in this backend that
waits on the public internet, so it can never run on the request thread.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from routers.auth import current_user
from routers.datasets import MAX_UPLOAD_BYTES, _TOO_LARGE_DETAIL, _reject_oversized_body
from services import genesis_jobs, osm_fetch
from services.datasets import DatasetStorageError
from services.genesis import phase_diagram, side_inputs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/genesis", tags=["genesis"])

_LANGUAGES = ("ko", "en", "ja")

#: Downloadable artifact kinds and the media type each is served as. A client
#: names a KIND, never a file: the mapping from kind to path lives in
#: ``services.genesis_jobs.artifact_path`` and is derived from the job record,
#: so no request can address a file this job did not produce.
_ARTIFACT_MEDIA_TYPES = {
    "inpx": "application/xml",
    "genesis_json": "application/json",
    "compliance_md": "text/markdown; charset=utf-8",
    "gate_json": "application/json",
    "gate_report": "text/markdown; charset=utf-8",
    # The optional layers' full findings. Present only for a generation that
    # carried side inputs — 404 otherwise, like any other artifact this job
    # never produced.
    "layers_json": "application/json",
    "source_osm": "application/xml",
}


def _language(request: Request) -> str:
    """ko unless the caller clearly asked for en/ja — ``?language=`` first, then
    ``Accept-Language``. Same convention as ``routers/auth.py``'s ``_language``."""
    requested = (request.query_params.get("language") or "").strip().lower()
    if requested in _LANGUAGES:
        return requested
    primary = (request.headers.get("accept-language") or "").split(",")[0].split("-")[0].strip().lower()
    return primary if primary in _LANGUAGES else "ko"


class StartGenesisJobRequest(BaseModel):
    """What ``POST /jobs`` takes: a staged upload plus the generation options.

    ``exclude_service`` is the real CLI surface (``genesis_osm_draft.py``'s
    ``--exclude-service``) and carries the same meaning: drop every
    ``highway=service`` way as a NAMED skip. The default is what the generator's
    own policy says (service ways ARE modelled), not what makes the output
    smaller.

    ``demand_token`` / ``signals_token`` are the optional layers, staged through
    ``POST /side-inputs`` and named here the same way the ``.osm`` is. Both
    default to ``None``, and a request that names neither takes the geometry
    -only path this endpoint has always taken, unchanged. Shared by BOTH entry
    routes on purpose: an upload (B1) and a region fetch (B2) both end at a
    staged ``.osm`` and this one request model, so neither can be the one that
    supports demand.
    """

    model_config = ConfigDict(extra="forbid")

    upload_token: str = Field(min_length=1, description="Token from POST /api/genesis/uploads.")
    name: str | None = Field(
        default=None,
        description="Base name for the artifacts. Omitted = the uploaded file's stem.",
    )
    exclude_service: bool = Field(
        default=False,
        description="Drop every highway=service way as a named skip (--exclude-service).",
    )
    keep_upload: bool = Field(
        default=False,
        description="Keep the staged .osm after the job starts (default: discard it).",
    )
    demand_token: str | None = Field(
        default=None,
        description=(
            "Token from POST /api/genesis/side-inputs?kind=demand. Adds the demand AND the "
            "§5.2.3 fleet layers (both are read from the one ImportResult)."
        ),
    )
    signals_token: str | None = Field(
        default=None,
        description=(
            "Token from POST /api/genesis/side-inputs?kind=signals. Adds fixed-time "
            "controllers, groups, heads and detectors."
        ),
    )
    keep_side_inputs: bool = Field(
        default=False,
        description="Keep the staged side inputs after the job starts (default: discard them).",
    )


class AttachLiveCheckRequest(BaseModel):
    """The live-check probe's ``state.json``, verbatim, plus which run to read.

    The state is passed as an opaque object: ``LoadOracle.from_probe_state``
    owns its shape, and re-declaring the probe's schema here would be a second
    copy to drift. ``label`` picks one run; omitted, the newest run carrying a
    load result wins (the probe's own rule).
    """

    model_config = ConfigDict(extra="forbid")

    state: dict[str, Any] = Field(description="genesis_open_live_check.py's state.json, verbatim.")
    label: str | None = Field(default=None, description="Which run inside that state.json.")


class BuildDemandInputRequest(BaseModel):
    """A staged counts token + the one join a human supplies → a ``demand``
    side input.

    ``approach_links`` is the SAME shape ``side_inputs.DemandSideInput`` takes
    (site id → {approach leg id → draft link id}) and is never defaulted here:
    an empty entry, or a leg left unmapped, stays empty and the underlying
    validator refuses it — this route invents no link id on a caller's behalf.
    ``demand_settings``/``fleet_settings`` are passed through as opaque objects
    rather than re-declared as pydantic models here, so the ONE place that
    knows their shape stays ``services.genesis.demand``/``fleet`` — the
    document this route builds is validated by the SAME
    ``side_inputs.parse_side_input`` the raw ``POST /side-inputs`` route uses,
    and that validator is the authority on whether they are well-formed.

    ``built_against_job`` is REQUIRED — link ids in ``approach_links`` are by
    definition read off ONE generation's map (a screen showing that job's
    draft), and ``draft.py`` reassigns them from a sequential counter on every
    build, so there is no such thing as a mapping that was not built against
    some specific generation. The route resolves that job's ``genesis_json``
    artifact IN THE CALLER'S WORKSPACE (404 on an unknown/foreign job, or one
    with no artifact — e.g. it errored), computes
    ``services.genesis.draft.link_table_fingerprint`` over it, and stamps both
    onto the document it stages. ``generate_demand`` compares this fingerprint
    against the draft it is actually asked to seed at generation time and
    refuses the whole generation on a mismatch — see roadmap v2 §5.B/G.
    """

    model_config = ConfigDict(extra="forbid")

    counts_token: str = Field(
        min_length=1, description="Token from POST /api/genesis/demand-inputs/parse."
    )
    approach_links: dict[str, dict[str, int]] = Field(
        description="site id → {approach leg id → draft link id}. Required; never guessed."
    )
    built_against_job: str = Field(
        min_length=1,
        description=(
            "The generation job whose link ids approach_links was read off. Required: a link id "
            "means nothing without the generation that assigned it."
        ),
    )
    window_start: datetime | None = Field(
        default=None, description="Analysis window start. Both ends or neither."
    )
    window_end: datetime | None = None
    demand_settings: dict[str, Any] | None = None
    fleet_settings: dict[str, Any] | None = None
    filename: str | None = Field(
        default=None, description="Name for the staged demand document. Omitted = 'demand.json'."
    )


# ---------------------------------------------------------------------------
# Mutations — authenticated
# ---------------------------------------------------------------------------


@router.post("/uploads")
async def upload_osm(
    request: Request,
    x_filename: str | None = Header(None),
    user: dict[str, Any] = Depends(current_user),
):
    """Stage ONE ``.osm`` extract and return its upload token."""
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "")
    _reject_oversized_body(request)
    content = await request.body()
    # Second check on the bytes actually received: a chunked upload carries no
    # Content-Length, so the header check cannot be the only gate.
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
    try:
        return genesis_jobs.stage_osm(filename, content)
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not write the uploaded .osm to the staging area. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


_SIDE_INPUT_TOO_LARGE_DETAIL = (
    f"The side input is larger than the "
    f"{side_inputs.MAX_SIDE_INPUT_BYTES // (1024 * 1024)} MB limit."
)


def _reject_oversized_side_input(request: Request) -> None:
    """413 on the DECLARED length, the same shape ``routers/datasets.py`` uses —
    with this route's own cap, because a side input is fully parsed on the
    request thread and a 250 MB one would occupy it for minutes."""
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        declared = int(raw_length)
    except ValueError:
        return
    if declared > side_inputs.MAX_SIDE_INPUT_BYTES:
        raise HTTPException(status_code=413, detail=_SIDE_INPUT_TOO_LARGE_DETAIL)


@router.post("/side-inputs")
async def upload_side_input(
    request: Request,
    kind: str,
    x_filename: str | None = Header(None),
    user: dict[str, Any] = Depends(current_user),
):
    """Stage ONE side-input document (``?kind=demand`` or ``?kind=signals``).

    The same two-step shape ``POST /uploads`` uses and for the same reason (no
    ``python-multipart`` in this backend): the raw JSON bytes are the body, the
    name arrives in ``X-Filename`` or ``?filename=``, and a token comes back.

    **Not a second door.** Authenticated like every other mutation here, capped
    (``side_inputs.MAX_SIDE_INPUT_BYTES``, far below the dataset limit because
    this body is fully parsed on the request thread), and structurally validated
    before the token exists — so a document that could never build a layer is a
    400 the uploader sees, not a job to poll. The response carries a COUNTED
    summary of what was read, so "the counts I sent" and "the counts that
    arrived" are comparable before a generation is spent.
    """
    if kind not in side_inputs.KINDS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "genesis_side_input_unknown_kind",
                "reasons": [
                    f"{kind!r} is not a side-input kind. Expected one of: "
                    + ", ".join(side_inputs.KINDS)
                ],
            },
        )
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "") or f"{kind}.json"
    # The declared length is checked against THIS route's own (much smaller)
    # cap, not the dataset one, so an over-large document is refused before its
    # bytes are read into the request thread's memory. The byte-level check
    # below is still needed: a chunked upload carries no Content-Length.
    _reject_oversized_side_input(request)
    content = await request.body()
    if len(content) > side_inputs.MAX_SIDE_INPUT_BYTES:
        raise HTTPException(status_code=413, detail=_SIDE_INPUT_TOO_LARGE_DETAIL)
    try:
        return genesis_jobs.stage_side_input(kind, filename, content)
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not write the side input to the staging area. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


_COUNTS_TOO_LARGE_DETAIL = (
    f"The counts workbook is larger than the "
    f"{side_inputs.MAX_SIDE_INPUT_BYTES // (1024 * 1024)} MB limit."
)


def _reject_oversized_counts(request: Request) -> None:
    """413 on the DECLARED length, the same shape ``_reject_oversized_side_input``
    uses — with the side-input cap, because a counts workbook is fully parsed on
    the request thread just like a side-input document is."""
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        declared = int(raw_length)
    except ValueError:
        return
    if declared > side_inputs.MAX_SIDE_INPUT_BYTES:
        raise HTTPException(status_code=413, detail=_COUNTS_TOO_LARGE_DETAIL)


@router.post("/demand-inputs/parse")
async def parse_demand_counts(
    request: Request,
    x_filename: str | None = Header(None),
    user: dict[str, Any] = Depends(current_user),
):
    """Stage ONE Daejeon counts workbook (``.xlsx``) and return what it read.

    The same two-step upload every mutating route in this router uses (no
    ``python-multipart`` in this backend): raw bytes as the body, the name in
    ``X-Filename`` or ``?filename=``. Parsed with
    ``services.dataimport.adapters.daejeon.DaejeonCountAdapter`` BEFORE a token
    exists — a caller who uploads the wrong workbook, or one this adapter does
    not recognise, finds out while they are still looking at the upload.

    This is deliberately NOT ``POST /side-inputs``: a counts workbook carries no
    ``approach_links`` (only a human can supply that join), so it is not a
    ``genesis.demand.v1`` document and cannot go through
    ``side_inputs.parse_side_input``. It stages in the SAME tree, under its own
    ``counts`` kind, and the token this route returns is meaningful to exactly
    one caller — ``POST /demand-inputs/build``.

    The adapter's own refusal semantics pass through VERBATIM in the response's
    ``summary.issues`` — never softened, never re-worded here — including a
    workbook the adapter reads but refuses entirely (0 sites, 0 counts): the
    reasons are the point, and a reviewer needs to see them before trying a
    different file.
    """
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "")
    _reject_oversized_counts(request)
    content = await request.body()
    if len(content) > side_inputs.MAX_SIDE_INPUT_BYTES:
        raise HTTPException(status_code=413, detail=_COUNTS_TOO_LARGE_DETAIL)
    try:
        return genesis_jobs.stage_counts(filename, content)
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not write the counts workbook to the staging area. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


@router.post("/demand-inputs/build")
def build_demand_input(
    payload: BuildDemandInputRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
):
    """Staged counts + a caller's approach→link map → a staged ``demand`` side input.

    Four steps, in order:

    1. Read back the staged counts (``POST /demand-inputs/parse``'s token) —
       404 if the token is unknown, expired, or names a different kind.
    2. Check ``approach_links`` against the counts THE SAME WAY
       ``services.genesis.demand.generate_demand`` will at generation time —
       through ``services.genesis.demand.check_approach_link_sites``, the exact
       function ``generate_demand`` itself calls, so a site or leg the mapping
       names that the counts do not contain refuses HERE, with that module's own
       code and wording, rendered in the CALLER'S language via ``_language``
       (the same convention the gate/report routes use). (Whether a mapped LINK
       exists is not checked here — that needs a draft, which does not exist
       until a generation runs; the job still catches it.)
    3. Resolve ``built_against_job``'s ``genesis_json`` artifact IN THE CALLER'S
       WORKSPACE (``genesis_jobs.artifact_path`` — 404 on an unknown/foreign job
       or one with no artifact at all, e.g. it errored) and compute its
       ``services.genesis.draft.link_table_fingerprint`` — the CRITICAL fix:
       draft link ids are a sequential counter reassigned on every build, so a
       mapping is only ever meaningful against the ONE generation it was read
       from.
    4. Build the ``genesis.demand.v1`` document — the staged counts verbatim
       plus this call's ``approach_links``/window/settings/fingerprint — and
       stage it through ``genesis_jobs.stage_side_input``, which runs it through
       the SAME ``side_inputs.parse_side_input`` the raw ``POST /side-inputs``
       route uses. Nothing here re-implements that validation or pre-empts it.
    """
    try:
        staged = genesis_jobs.staged_counts(payload.counts_token)
    except genesis_jobs.StagedOsmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    import json as _json

    from services.dataimport.demand import build_demand_seeding
    from services.genesis.demand import check_approach_link_sites
    from services.genesis.draft import DraftNetwork, link_table_fingerprint

    window = (
        (payload.window_start, payload.window_end)
        if payload.window_start is not None and payload.window_end is not None
        else None
    )
    seeding = build_demand_seeding(staged["result"], window=window)
    observed_sites = sorted({profile.site_id for profile in seeding.profiles})
    observed_approaches: dict[str, set[str]] = {}
    for profile in seeding.profiles:
        observed_approaches.setdefault(profile.site_id, set()).add(profile.approach_leg_id)

    site_check = check_approach_link_sites(
        payload.approach_links,
        observed_sites=observed_sites,
        observed_approaches=observed_approaches,
    )
    if site_check.issues:
        language = _language(request)
        raise HTTPException(
            status_code=400,
            detail={
                "code": site_check.issues[0].code,
                "reasons": [
                    issue.messages.get(language) or issue.message for issue in site_check.issues
                ],
            },
        )

    try:
        census_path = genesis_jobs.artifact_path(payload.built_against_job, "genesis_json")
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        built_against_draft = DraftNetwork.model_validate(
            _json.loads(census_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job {payload.built_against_job}'s genesis_json artifact could not be read as a "
                f"draft network. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    link_table_sha256 = link_table_fingerprint(built_against_draft)

    document: dict[str, Any] = {
        "kind": side_inputs.DEMAND_DOC_KIND,
        "counts": staged["result"].model_dump(mode="json"),
        "approach_links": payload.approach_links,
        "built_against_job": payload.built_against_job,
        "built_against_link_table_sha256": link_table_sha256,
    }
    if payload.window_start is not None:
        document["window_start"] = payload.window_start.isoformat()
    if payload.window_end is not None:
        document["window_end"] = payload.window_end.isoformat()
    if payload.demand_settings is not None:
        document["demand_settings"] = payload.demand_settings
    if payload.fleet_settings is not None:
        document["fleet_settings"] = payload.fleet_settings

    content = _json.dumps(document, ensure_ascii=False).encode("utf-8")
    name = Path(str(payload.filename or "demand.json")).name
    try:
        return genesis_jobs.stage_side_input(side_inputs.KIND_DEMAND, name, content)
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The demand document could not be staged. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


def _resolve_side_input(token: str | None, expected_kind: str):
    """A staged side-input token → its frozen path, or an HTTP refusal.

    The KIND is checked against what the field meant: a demand document named in
    ``signals_token`` would otherwise be handed to the signals builder, which
    would refuse for a reason ("no assignments") that says nothing about the
    real mistake.
    """
    if not token:
        return None
    try:
        staged = genesis_jobs.staged_side_input(token)
    except genesis_jobs.StagedOsmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if staged["kind"] != expected_kind:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "genesis_side_input_kind_mismatch",
                "reasons": [
                    f"That token names a {staged['kind']!r} side input, but it was given as the "
                    f"{expected_kind!r} one."
                ],
            },
        )
    return staged["path"]


@router.post("/jobs", status_code=202)
def start_job(
    payload: StartGenesisJobRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Start one generation. 202: the work happens on the job queue's thread.

    Returns the job record immediately; the caller polls ``GET /jobs/{id}``.
    Nothing is registered as a dataset here — ``gate_and_publish`` decides that,
    inside the job, after the gate has run.

    With ``demand_token``/``signals_token`` the generation composes those layers
    into ONE ``.inpx`` (the shape ``445477d`` proved a real Vissim opens) and the
    gate judges THAT file — so V5 and V6, which report NOT_CHECKABLE on a
    geometry-only network, actually run. A side input that refuses refuses the
    generation: the job lands ``error`` with every reason named, never a network
    quietly missing the layer somebody supplied data for.
    """
    try:
        osm_path = genesis_jobs.staged_osm_path(payload.upload_token)
    except genesis_jobs.StagedOsmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    demand_path = _resolve_side_input(payload.demand_token, side_inputs.KIND_DEMAND)
    signals_path = _resolve_side_input(payload.signals_token, side_inputs.KIND_SIGNALS)

    try:
        record = genesis_jobs.start_genesis_job(
            osm_path=osm_path,
            name=payload.name,
            exclude_service=payload.exclude_service,
            source_filename=osm_path.name,
            submitted_by=str(user.get("username") or ""),
            demand_input=demand_path,
            signals_input=signals_path,
        )
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except genesis_jobs.GenesisRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        # The staged .osm is copied into the job's work directory before the
        # thread starts; a disk that refuses that copy is the same "nothing
        # changed, resolve it and retry" answer routers/datasets.py gives.
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not prepare the generation's work directory. Nothing was started. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc

    if not payload.keep_upload:
        genesis_jobs.discard_staged_osm(payload.upload_token)
    if not payload.keep_side_inputs:
        # Only after the job exists: ``create_genesis_job`` has already copied
        # them into its work directory, so discarding the staged originals
        # cannot change the input out from under the running generation.
        for token in (payload.demand_token, payload.signals_token):
            if token:
                genesis_jobs.discard_staged_side_input(token)
    logger.info(
        "genesis: %s started generation %s (%s) in workspace %s%s",
        user.get("username"),
        record["id"],
        record["name"],
        record["workspace_id"],
        (
            f" with side inputs {sorted(record['side_inputs'])}"
            if record.get("side_inputs")
            else ""
        ),
    )
    return record


@router.post("/jobs/{job_id}/live-check")
def attach_live_check(
    job_id: str,
    payload: AttachLiveCheckRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Record an operator's ATTESTATION that a real Vissim loaded this artifact.

    Deliberately not called "proof". This body arrives over HTTP, so the app
    cannot establish that Vissim ever ran — a caller who knows the artifact can
    hand-write ``load_ok: true`` about it, and an earlier revision of this
    docstring wrongly claimed otherwise. What IS enforced: the digest compared
    is always of THIS job's stored artifact (the server never hashes a
    client-named path), the result is stamped ``operator_attested`` with the
    caller's name, and a V1 that goes green this way is labelled as resting on
    that attestation everywhere it surfaces.

    A downgrade takes the same road as an upgrade — evidence that the load
    FAILED refuses the artifact and revokes its publication.
    """
    try:
        return genesis_jobs.attach_live_check(
            job_id,
            payload.state,
            label=payload.label,
            attested_by=str(user.get("username") or user.get("id") or "unknown"),
        )
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatasetStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except genesis_jobs.GenesisJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/side-inputs")
def list_side_inputs(user: dict[str, Any] = Depends(current_user)):
    """Every side input currently staged in this workspace — the picker data
    for the job-start form's demand/signals dropdowns.

    AUTHENTICATED, unlike this router's other reads. Every other GET in this
    file returns the record of a decision this app already made (a job's
    status, a stored gate verdict) and stays open in parity with
    ``/api/simulations``. This one is different in the same way the
    ``phase-diagrams`` sub-tree is: it lists client data BEFORE any decision
    has been made about it — filenames, per-site leg ids, vehicle-class codes
    and adapter refusal text taken straight from an uploaded workbook. An
    anonymous caller has no more business enumerating that than they do
    downloading a generation's artifact (the reason
    ``GET /jobs/{job_id}/artifact/{kind}`` is guarded), so this route takes
    ``current_user`` too.

    Workspace-scoped the same way every staged token already is:
    ``genesis_jobs.side_staging_dir()`` is derived from the CURRENT bound
    workspace on every call, so this can only ever list — and a client can
    only ever resolve a token against — the caller's own project's staging
    tree.
    """
    return {"side_inputs": genesis_jobs.list_staged_side_inputs()}


@router.get("/jobs")
def list_jobs():
    """This workspace's generations, newest first."""
    return {"jobs": genesis_jobs.list_jobs()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return genesis_jobs.get_job(job_id)
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except genesis_jobs.GenesisRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/gate")
def get_gate_report(job_id: str, request: Request):
    """The stored verdict, check by check, ko primary.

    Served from the job's ``gate.json`` rather than re-run, so what a reviewer
    reads is the verdict that actually decided whether the artifact was
    published. 409 (not 404) while a job is still generating: the job exists and
    its gate result does not exist YET, which is a different answer from "no
    such job".
    """
    try:
        return genesis_jobs.gate_payload(job_id, language=_language(request))
    except genesis_jobs.GenesisJobNotFoundError as exc:
        record = None
        try:
            record = genesis_jobs.get_job(job_id)
        except genesis_jobs.GenesisJobError:
            record = None
        if record is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This generation has not been gated yet (status: {record.get('status')})."
                ),
            ) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"The stored gate result could not be read. [{type(exc).__name__}: {exc}]",
        ) from exc


@router.get("/jobs/{job_id}/artifact/{kind}")
def download_artifact(
    job_id: str, kind: str, user: dict[str, Any] = Depends(current_user)
):
    """One artifact file by KIND — never by a client-supplied filename.

    AUTHENTICATED, unlike this router's other reads. The rest of them return
    verdicts and metadata; this one returns the network itself. An adversarial
    pass showed why that matters: ``POST /api/datasets/upload`` is the app's
    pre-existing, un-gated upload path, so an anonymous caller who could
    download a REFUSED artifact could re-upload those exact bytes and make the
    refused network the project's active dataset — laundering a proven fault
    straight past the gate this unit exists to enforce. Requiring a session
    does not fix that upload path (see DECISIONS 2026-08-11: it is tracked with
    the app-wide-login decision), but it stops this router from handing the
    bytes to someone with no identity at all.
    """
    if kind not in _ARTIFACT_MEDIA_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown artifact kind {kind!r}. Expected one of: "
            + ", ".join(sorted(_ARTIFACT_MEDIA_TYPES)),
        )
    try:
        path = genesis_jobs.artifact_path(job_id, kind)
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path, media_type=_ARTIFACT_MEDIA_TYPES[kind], filename=path.name
    )


@router.get("/jobs/{job_id}/report", response_class=PlainTextResponse)
def get_gate_report_markdown(job_id: str, request: Request):
    """The gate's own markdown report, in the requested language.

    ``gate.report.md`` on disk is always the ko render (it is written at gate
    time, before anyone has asked a language); this route re-renders the STORED
    result for en/ja rather than re-running the gate.
    """
    try:
        payload = genesis_jobs.gate_payload(job_id, language=_language(request))
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"The stored gate result could not be read. [{type(exc).__name__}: {exc}]",
        ) from exc
    return PlainTextResponse(payload["report_markdown"], media_type="text/markdown; charset=utf-8")


# ---------------------------------------------------------------------------
# Region → .osm extraction (roadmap v2 §5.B row B2)
# ---------------------------------------------------------------------------


class StartRegionFetchRequest(BaseModel):
    """A bounding box plus an optional name for the extract.

    ``extra="forbid"``, and the endpoint is NOT in this model on purpose: the
    Overpass instance is server configuration (``PRISM_OVERPASS_ENDPOINT``), and
    a caller-supplied URL would turn this route into an SSRF instrument pointed
    at whatever the server can reach.
    """

    model_config = ConfigDict(extra="forbid")

    south: float = Field(description="Southern latitude, WGS84 degrees.")
    west: float = Field(description="Western longitude, WGS84 degrees.")
    north: float = Field(description="Northern latitude, WGS84 degrees.")
    east: float = Field(description="Eastern longitude, WGS84 degrees.")
    name: str | None = Field(
        default=None,
        description="Base name for the staged extract. Omitted = derived from the centre.",
    )

    def bbox(self) -> osm_fetch.BoundingBox:
        return osm_fetch.BoundingBox(
            south=self.south, west=self.west, north=self.north, east=self.east
        )


def _region_refusal(exc: osm_fetch.OsmFetchRefused) -> HTTPException:
    """One 400 shape for every region refusal — the same ``{code, reasons}``
    body ``stage_osm``'s refusals use, plus the measured numbers that made it a
    refusal. "Too large" without a number is not actionable."""
    return HTTPException(
        status_code=400,
        detail={"code": exc.code, "reasons": exc.reasons, "measurement": exc.detail},
    )


@router.get("/region/limits")
def get_region_limits(request: Request):
    """The caps in force, optionally at a stated latitude. Pure arithmetic.

    Open, like this router's other reads: it computes the projection's validity
    radius and this app's response-byte cap and makes no network call at all.
    """
    def _number(name: str, default: float) -> float:
        raw = request.query_params.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"{name}={raw!r} is not a number."
            ) from exc

    return osm_fetch.region_limits(_number("lat", 0.0), _number("lon", 0.0))


@router.get("/region/preview")
def preview_region(
    south: float, west: float, north: float, east: float
):
    """Measure a region against cap 1 WITHOUT fetching anything.

    This is what lets a browser show the area and the estimated size against the
    limit before a byte is downloaded. It answers 200 with
    ``within_region_cap: false`` for a region that is too large rather than
    400-ing: a preview whose whole job is to describe an over-large box must be
    able to describe an over-large box.
    """
    try:
        bbox = osm_fetch.BoundingBox(south=south, west=west, north=north, east=east)
    except osm_fetch.OsmFetchRefused as exc:
        raise _region_refusal(exc) from exc
    return osm_fetch.measure_region(bbox).as_dict()


@router.post("/region/fetches", status_code=202)
def start_region_fetch(
    payload: StartRegionFetchRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Start ONE region fetch. 202: the Overpass call happens on a worker thread.

    Authenticated for the same reason ``POST /uploads`` is — this writes into the
    project's tree, and it also spends the box's one outbound connection to a
    third-party service, which is not something an anonymous caller gets to do.
    Cap 1 is checked HERE, synchronously, so a region that could never be
    fetched is a 400 the caller sees immediately instead of a job to poll.
    """
    try:
        record = osm_fetch.start_region_fetch(
            bbox=payload.bbox(),
            name=payload.name,
            submitted_by=str(user.get("username") or ""),
        )
    except osm_fetch.OsmFetchRefused as exc:
        raise _region_refusal(exc) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not write the region fetch record. Nothing was started. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    logger.info(
        "genesis: %s started region fetch %s (%s) in workspace %s",
        user.get("username"),
        record["id"],
        record["name"],
        record["workspace_id"],
    )
    return record


@router.get("/region/fetches")
def list_region_fetches():
    """This workspace's region fetches, newest first."""
    return {"fetches": osm_fetch.list_fetches()}


@router.get("/region/fetches/{fetch_id}")
def get_region_fetch(fetch_id: str):
    try:
        return osm_fetch.get_fetch(fetch_id)
    except osm_fetch.OsmFetchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except osm_fetch.OsmFetchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 현시도 review — roadmap C1 option (c), the browser half
# ---------------------------------------------------------------------------
#
# The offline tool (``backend/tools/genesis_phase_diagram_batch.py``) renders
# each 대전 sheet's 현시도 band and asks the vision model for a reading. These
# routes are where a NAMED HUMAN turns that reading into a fact, and where the
# confirmed mapping becomes a ``signals`` side input.
#
# **Every route here is authenticated, and the whole sub-tree for one reason:**
# it serves client-confidential material (the rendered sheet image, the
# intersection's own name and number, the extracted sheet text) and the act of
# confirming it. Unlike the gate reads above — which return the record of a
# decision and stay open — there is nothing in this sub-tree that is merely a
# verdict about our own artifact. And ``POST /confirmation`` is the load-bearing
# one: an attestation records WHO said the mapping is right, so a caller with no
# identity cannot make one at all.
#
# No proposal is generated here. Vision calls take seconds to tens of seconds
# and are made by the offline tool, never on a request thread.


#: Where reviews live inside a workspace's genesis tree. Matches the offline
#: tool's ``STORE_DIRNAME`` — the tool writes what these routes read.
PHASE_DIAGRAM_DIRNAME = "phase-diagrams"


def _phase_diagram_store() -> phase_diagram.PhaseDiagramStore:
    """The review store for the CURRENT workspace, derived per call.

    Same rule as ``genesis_jobs.genesis_dir()``: the workspace is resolved from
    the bound context on every call rather than captured once, so one project's
    reviews can never be served under another's binding.
    """
    return phase_diagram.PhaseDiagramStore(
        genesis_jobs.genesis_dir() / PHASE_DIAGRAM_DIRNAME
    )


def _phase_diagram_refusal(exc: phase_diagram.PhaseDiagramRefused) -> HTTPException:
    """One HTTP shape for every 현시도 refusal, with the status the code means.

    404 for "no such review", 409 for a review that exists but is not in a state
    the request needs (unconfirmed, stale). Both carry the same
    ``{code, reasons}`` body every other refusal in this router uses, so a
    browser translates the CODE and shows the reasons verbatim.
    """
    status = 404 if exc.code == "phase_diagram_unknown_review" else 409
    return HTTPException(status_code=status, detail={"code": exc.code, "reasons": exc.reasons})


class ConfirmPhaseDiagramRequest(BaseModel):
    """One human's confirmation of one intersection's approach→phase mapping.

    ``attested_by`` is NOT here: it is taken from the signed-in session, never
    from the request body. A caller naming somebody else as the attester is the
    one thing an attestation must not permit, and the cheapest way to make that
    impossible is to give the client no field to put a name in.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: int = Field(description="The draft node this sheet's intersection is.")
    approach_phases: dict[str, str] = Field(
        description="draft approach link id → plan phase id ('Φ1'). The mapping itself."
    )
    approach_links: dict[str, str] = Field(
        default_factory=dict,
        description="draft link id → the sheet approach label bound to it, for the record.",
    )
    level_no: int = Field(description="Which 수준 (cycle) this mapping is confirmed for.")
    plan_id: int = Field(description="Which 시간분할 ID this mapping is confirmed for.")
    image_sha256: str = Field(
        min_length=64,
        max_length=64,
        description="The image the human actually looked at. Checked against the stored render.",
    )
    source_sha256: str = Field(min_length=64, max_length=64)
    proposal_sha256: str | None = Field(
        default=None, description="None = confirmed with no proposal (blank manual entry)."
    )
    proposal_outcome: Literal["proposed", "refused", "none"] = "none"
    declared_amber_s: float | None = Field(default=None, gt=0)
    amber_declaration_note: str | None = None
    changes: list[phase_diagram.MappingChange] = Field(default_factory=list)
    note: str | None = None


class BuildSignalsInputRequest(BaseModel):
    """Which confirmed reviews become one ``genesis.signals.v1`` side input."""

    model_config = ConfigDict(extra="forbid")

    review_ids: list[str] = Field(
        min_length=1, description="Confirmed reviews. An unconfirmed one is a 409, never a skip."
    )
    filename: str | None = Field(default=None, description="Name for the staged document.")


@router.get("/phase-diagrams")
def list_phase_diagrams(user: dict[str, Any] = Depends(current_user)):
    """Every rendered 현시도 review in this workspace, with its state.

    ``confirmed`` is the only field that means a mapping is usable, and it is
    false both for "nobody confirmed" and for "the confirmation went stale" —
    ``stale_reasons`` says which, in Korean, so the UI never has to guess.
    """
    return {"reviews": _phase_diagram_store().index_payload()}


@router.get("/phase-diagrams/{review_id}")
def get_phase_diagram(review_id: str, user: dict[str, Any] = Depends(current_user)):
    """One review: render provenance, the proposal (or null), the cross-check,
    the attestation (or null), and what the sheet's text layer says."""
    try:
        return _phase_diagram_store().review_payload(review_id)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"The stored review could not be read. [{type(exc).__name__}: {exc}]",
        ) from exc


@router.get("/phase-diagrams/{review_id}/image")
def get_phase_diagram_image(review_id: str, user: dict[str, Any] = Depends(current_user)):
    """The rendered 현시도 band, as a PNG. Client-confidential material."""
    try:
        path = _phase_diagram_store().image_path(review_id)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/phase-diagrams/{review_id}/network-candidates")
def get_phase_diagram_candidates(
    review_id: str,
    job_id: str,
    user: dict[str, Any] = Depends(current_user),
):
    """Draft nodes ranked as CANDIDATES for this sheet's intersection.

    A ranked list with its reason attached, never a binding: the ranking is a
    plain name overlap against 교차로명, and each entry carries ``match_reason``
    so a reviewer sees why a node is at the top instead of being told it is the
    right one. Each candidate carries its approach links with a computed
    bearing, because ``approach_phases`` is keyed by LINK id and a human cannot
    bind what they cannot see.
    """
    from services.genesis.draft import DraftNetwork

    store = _phase_diagram_store()
    try:
        sheet = store.sheet(review_id)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    try:
        census = genesis_jobs.artifact_path(job_id, "genesis_json")
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        import json as _json

        draft = DraftNetwork.model_validate(_json.loads(census.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"That generation's draft could not be read. [{type(exc).__name__}: {exc}]",
        ) from exc
    return {
        "review_id": review_id,
        "job_id": job_id,
        "intersection_name": sheet.intersection_name,
        "candidates": phase_diagram.node_candidates(
            draft, intersection_name=sheet.intersection_name
        ),
    }


@router.post("/phase-diagrams/{review_id}/confirmation")
def confirm_phase_diagram(
    review_id: str,
    payload: ConfirmPhaseDiagramRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Record ONE human's attestation for one intersection. The load-bearing act.

    The attester is the signed-in user and the timestamp is the server's — a
    claim about who said what, dated by the client, is not an attestation. The
    shas in the body are the ones the human's screen was showing; the store
    compares them against what is on disk NOW and refuses
    (``phase_diagram_attestation_stale`` → *재확인 필요*) rather than recording a
    confirmation of a drawing that has since been re-rendered.
    """
    store = _phase_diagram_store()
    try:
        attestation = phase_diagram.MappingAttestation(
            attested_by=str(user.get("username") or user.get("id") or "").strip(),
            attested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source_file=store.render(review_id).source_file,
            source_sha256=payload.source_sha256,
            image_sha256=payload.image_sha256,
            proposal_sha256=payload.proposal_sha256,
            proposal_outcome=payload.proposal_outcome,
            node_id=payload.node_id,
            approach_links=payload.approach_links,
            approach_phases=payload.approach_phases,
            level_no=payload.level_no,
            plan_id=payload.plan_id,
            declared_amber_s=payload.declared_amber_s,
            amber_declaration_note=payload.amber_declaration_note,
            changes=payload.changes,
            note=payload.note,
        )
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "phase_diagram_confirmation_invalid", "reasons": [str(exc)]},
        ) from exc
    try:
        store.put_attestation(review_id, attestation)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The confirmation could not be written. Nothing was recorded. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    return store.review_payload(review_id)


@router.post("/phase-diagrams/signals-input")
def build_phase_diagram_signals_input(
    payload: BuildSignalsInputRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Confirmed reviews → a staged ``signals`` side input, ready for ``POST /jobs``.

    The document is built HERE and staged through ``genesis_jobs.stage_side_input``
    — the same door ``POST /side-inputs`` uses, with the same validation — so the
    browser never assembles a ``genesis.signals.v1`` document itself. Two reasons,
    and both are the point of this unit: a second implementation in TypeScript
    could drift from the one ``side_inputs.py`` validates, and more importantly the
    "an unconfirmed proposal is inert" rule would become a client-side convention
    instead of a server-side refusal.

    A review that is not confirmed, or whose confirmation is stale, is a 409
    naming it. It is never skipped, and the remaining reviews are never staged
    without it: a signals document silently missing one intersection is a network
    with one junction quietly unsignalised.
    """
    store = _phase_diagram_store()
    confirmed: list[phase_diagram.ConfirmedMapping] = []
    for review_id in payload.review_ids:
        try:
            confirmed.append(store.confirmed_mapping(review_id))
        except phase_diagram.PhaseDiagramRefused as exc:
            raise _phase_diagram_refusal(exc) from exc
    try:
        document = phase_diagram.build_signals_document(confirmed)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc

    import json as _json

    content = _json.dumps(document, ensure_ascii=False).encode("utf-8")
    name = Path(str(payload.filename or "phase-diagrams.signals.json")).name
    try:
        return genesis_jobs.stage_side_input(side_inputs.KIND_SIGNALS, name, content)
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The signals document could not be staged. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


__all__ = ["router"]

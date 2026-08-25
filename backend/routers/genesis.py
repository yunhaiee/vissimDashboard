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
``LoadNet``, and it arrives later — two routes, one rule.
``POST /jobs/{id}/live-check`` takes an operator's ``state.json`` and records it
as their ATTESTATION. ``POST /jobs/{id}/live-check/run`` (unit #33) instead
ENQUEUES a job that opens the artifact in a real Vissim itself and re-gates the
parent on what it measured; this handler only mints and starts that job — the
COM turn runs on the job queue's worker thread, exactly like
``POST /jobs/{id}/fixes/apply``. The rule both share: the evidence is judged by
``gate.py``'s unmodified ``LoadOracle``, and this router judges nothing.

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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from routers.auth import current_user
from routers.datasets import MAX_UPLOAD_BYTES, _TOO_LARGE_DETAIL, _reject_oversized_body
from services import genesis_jobs, osm_fetch
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes
from services.datasets import DatasetStorageError
from services.genesis import geojson as genesis_geojson
from services.genesis import osm as genesis_osm
from services.genesis import phase_diagram, phase_template, side_inputs

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


class GenesisFixAttestationRequest(BaseModel):
    """Unit #35 — a human's claim that the real road continues, and where to.

    The ONE judgment call the V3 connectivity treatment accepts. Both fields
    are required and neither has a default: an attestation without a target is
    not an attestation, and an attestation without a basis is a click. Who
    attested it and when are deliberately NOT fields here — the same
    discipline ``approved_by`` follows on the parent model, for the same
    reason: the cheapest way to make "a caller names somebody else as the
    attester" impossible is to give the client nowhere to put a name.
    ``services.genesis_jobs.create_genesis_fix_job`` stamps both from the
    signed-in session and the server's own clock.
    """

    model_config = ConfigDict(extra="forbid")

    to_link: str = Field(
        min_length=1,
        description="The draft link id the attester says the real road continues to.",
    )
    basis: str = Field(
        min_length=1,
        max_length=600,
        description="Why the attester knows it continues -- their own words, recorded verbatim.",
    )


class ApplyGenesisFixRequest(BaseModel):
    """One approved gate-finding fix -> a sibling fix job.

    ``approved_by`` is deliberately NOT a field here, the same discipline
    ``ConfirmPhaseDiagramRequest`` uses for ``attested_by``: an approval is an
    attestation of who signed off on spending live COM time on this edit, and
    the cheapest way to make "a caller names somebody else as the approver"
    impossible is to give the client no field to put a name in. The route
    stamps the signed-in session's own username instead.

    ``finding_code``/``verb`` are re-validated against the registry HERE,
    before any job is touched (:func:`services.agent.genesis_fixes.get_genesis_fix`)
    — a caller must not be able to apply an arbitrary verb under a finding
    whose mapping names a different one, or ANY verb under a finding this
    catalogue declares :data:`services.agent.fixes.NOT_AUTOMATABLE`. Whether
    the parent job is actually refused, and whether ``verb``/``params`` still
    resolve against the LIVE tool registry, is
    :func:`services.genesis_jobs.create_genesis_fix_job`'s own job -- this
    model does not re-implement that check.
    """

    model_config = ConfigDict(extra="forbid")

    finding_code: str = Field(min_length=1, description="One of gate.py's own finding codes.")
    subject: str | None = Field(
        default=None,
        description=(
            "The finding's own subject (e.g. the vehicle input number), naming WHICH "
            "finding when the same code appears more than once in the parent's verdict. "
            "Optional for backward compatibility -- omitting it on a duplicated code is "
            "refused by name (genesis_fix_finding_ambiguous_without_subject), never "
            "silently resolved to the first match. Unit #30."
        ),
    )
    verb: str = Field(min_length=1, description="Must match the finding's registered verb exactly.")
    params: dict[str, Any] = Field(
        default_factory=dict, description="The edit verb's params, approved by a human."
    )
    approval_rationale: str | None = Field(
        default=None, description="Why the approver is confident in this fix, for the audit trail."
    )
    attestation: GenesisFixAttestationRequest | None = Field(
        default=None,
        description=(
            "Unit #35: for a V3 silent-departure finding whose SOURCE shares the gap, the "
            "approver's own attestation that the real road continues. Absent (the default) "
            "the registry's re-derivation reaches no proposal for such a finding and the "
            "apply is refused -- no attestation, no proposal."
        ),
    )


class ConfirmDeadEndRequest(BaseModel):
    """Unit #42 — a human's claim that the real road ENDS here, and nothing else.

    The mirror image of :class:`GenesisFixAttestationRequest`, and it keeps the
    same three disciplines for the same reasons. WHO confirmed it and WHEN are
    deliberately NOT fields: the route stamps the signed-in session's own name
    and the server's clock, so a caller has nowhere to put somebody else's name.
    ``basis`` is required with no default, because a confirmation without a
    stated basis is a click. And nothing here names what to REMOVE beyond the
    finding itself — the way id is re-derived server-side from the finding's own
    census record, so a caller cannot widen an exclusion past the road they
    actually looked at.

    ``acknowledged_collateral_way_ids`` is the one echo: the ways the confirming
    reader was SHOWN would come loose with this one (empty for the ordinary leaf
    case). The service re-derives the set and refuses a submission that does not
    match, the same re-derive-and-compare rule unit #35 applies to connector
    params — a stale screen must not be able to smuggle an unseen severance past
    the person signing for it.
    """

    model_config = ConfigDict(extra="forbid")

    finding_code: str = Field(min_length=1, description="One of gate.py's own finding codes.")
    subject: str | None = Field(
        default=None,
        description=(
            "The finding's own subject, naming WHICH finding when the same code appears more "
            "than once in the parent's verdict. Omitting it on a duplicated code is refused by "
            "name (genesis_fix_finding_ambiguous_without_subject), never silently resolved."
        ),
    )
    basis: str = Field(
        min_length=1,
        max_length=genesis_jobs.DEAD_END_BASIS_MAX_CHARS,
        description=(
            "How the confirmer knows the road ends -- their own words, recorded verbatim into "
            "the skip ledger (e.g. 'Google Maps site check -- the road runs inside the "
            "department store as an internal circulation loop')."
        ),
    )
    acknowledged_collateral_way_ids: list[int] = Field(
        default_factory=list,
        description=(
            "The OSM way ids the confirming reader was shown would ALSO be cut loose by this "
            "exclusion. Must equal the server's own re-derivation exactly."
        ),
    )


class RegenerateJobRequest(BaseModel):
    """The requester's own sentence, and nothing else.

    A regeneration takes NO parameters by design: it re-derives the parent's own
    source with the parent's own options through the corrected converter, and
    every field a caller could supply here would be a way to change the input
    while calling the result a treatment. ``reason`` is carried into the
    lineage verbatim for the audit trail; WHO requested it is stamped
    server-side from the session, exactly as unit #35's attestation is, so
    attribution cannot be forged from the body.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Why this generation is being re-derived, for the audit trail.",
    )


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


@router.post("/jobs/{job_id}/live-check/run", status_code=202)
def start_live_check(
    job_id: str,
    user: dict[str, Any] = Depends(current_user),
):
    """Queue a job that opens THIS generation's artifact in a real Vissim. 202.

    Unit #33 — the answer to "why does closing V1 need somebody at a console?".
    The sibling route above (``POST .../live-check``) records an operator's
    attestation about a probe they ran themselves; this one makes the app run
    the probe: ``genesis_jobs.start_live_check_job`` clones the artifact into a
    NEW job's work directory and starts that job on a daemon thread, and the
    returned record's ``id`` is what the caller polls through
    ``GET /jobs/{id}``. **Zero COM in this handler** — the whole point of the
    202 (CLAUDE.md: all COM work goes through the job queue, never a request
    handler), and the same split ``POST /jobs`` and ``POST .../fixes/apply``
    already use.

    Authenticated like every other mutation here: the run spends live Vissim
    time against a project's job tree, and ``derived_from.requested_by`` on the
    record is why a session is required at all.

    Refusals are NAMED, never a disabled button (409 with ``{code, reasons}``):
    ``genesis_live_check_parent_not_gated`` when the generation has no gated
    artifact to open yet, ``genesis_live_check_artifact_missing`` when it
    reached a verdict but its ``.inpx`` is gone. Both come from
    ``create_live_check_job``, the one layer every caller passes through.
    """
    try:
        record = genesis_jobs.start_live_check_job(
            job_id,
            requested_by=str(user.get("username") or user.get("id") or "unknown"),
        )
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except genesis_jobs.GenesisRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        # Cloning the parent's artifact is synchronous, request-thread work; a
        # disk that refuses it is the same "nothing changed, retry" answer
        # ``POST /jobs`` gives for the same step.
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not prepare the live check's work directory. Nothing was started. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    logger.info(
        "genesis: %s queued a live check of job %s -> live-check job %s",
        user.get("username"),
        job_id,
        record["id"],
    )
    return record


@router.post("/jobs/{job_id}/regenerate", status_code=202)
def regenerate_job(
    job_id: str,
    payload: RegenerateJobRequest | None = None,
    user: dict[str, Any] = Depends(current_user),
):
    """Treat a finding by RE-DERIVING the generation from its own source. 202.

    Unit #38, and the third disposition beside "apply this verb" and "there is
    no automated fix": :data:`services.agent.genesis_fixes.REGENERATE_WITH_FIX`.
    Some findings cannot be edited away honestly — unit #34 measured that every
    real ``v7_links_share_one_polyline`` finding's draft twins are byte-identical,
    so the corruption predates the draft and no census-derived coordinate exists
    to correct it. Unit #38 fixed what upstream caused it (a source declaring
    one way id twice), and this route is how an artifact refused under that
    finding gets the only honest treatment: the SAME source bytes, the
    corrected converter, the same unmodified gate.

    **Nothing is invented.** ``genesis_jobs.create_regenerate_job`` copies the
    parent's own ``source.osm`` (sha-verified against the digest the parent
    recorded for it), carries the parent's own ``options`` verbatim, and runs
    the NORMAL generation runner. The parent is byte-untouched — this route
    only reads it — and the regenerated job gates on its own merits, exactly
    like a sibling fix job.

    Refusals are NAMED, never a disabled button (409 with ``{code, reasons}``):
    ``genesis_regenerate_parent_not_gated``, ``genesis_regenerate_source_missing``,
    ``genesis_regenerate_source_sha_mismatch``, and — the eligibility one —
    ``genesis_regenerate_not_eligible`` when the parent's verdict carries no
    finding whose disposition is ``REGENERATE_WITH_FIX``. That last refusal is
    what keeps this from becoming a re-roll button: a finding the CENSUS can
    correct has unit #34's ``POST .../fixes/apply`` path instead, and the
    refusal says so.

    Authenticated like every other mutation here: the run spends a project's
    job tree, and ``derived_from.requested_by`` on the record is why a session
    is required at all. The body carries at most the requester's own reason —
    never who they are (stamped server-side) and never an input.
    """
    try:
        record = genesis_jobs.start_regenerate_job(
            job_id,
            requested_by=str(user.get("username") or user.get("id") or "unknown"),
            reason=(payload.reason if payload is not None else None),
        )
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except genesis_jobs.GenesisRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        # Copying the parent's source is synchronous, request-thread work; a
        # disk that refuses it is the same "nothing changed, retry" answer
        # ``POST .../live-check/run`` gives for its own clone step.
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not prepare the regeneration's work directory. Nothing was started. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    logger.info(
        "genesis: %s queued a regeneration of job %s -> job %s",
        user.get("username"),
        job_id,
        record["id"],
    )
    return record


@router.post("/jobs/{job_id}/dead-end-confirmation", status_code=202)
def confirm_real_world_dead_end(
    job_id: str,
    payload: ConfirmDeadEndRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Record that a human CONFIRMED the road really ends, and re-derive without
    that way. 202.

    Unit #42, and the answer to the one V3 world gap unit #35's question cannot
    reach. A ``v3_silent_departure_point`` whose SOURCE shares the gap gets asked
    *does the real road continue, and to where?* — but some of those roads
    genuinely end (measured on job ``2712071437d0``: a 6.5 m service stub into a
    department store's car park, whose only reachable candidate is 9.7 m away on
    a different road). Attesting to that candidate would put false geometry in
    the model, and leaving the finding untreated leaves the artifact refused
    forever. This route is the third door: a person who checked the site says so,
    the confirmed way is excluded BY NAME, and the generation is re-derived
    without it.

    **The judge is untouched and no finding is suppressed.** V3 stops firing for
    that link because the way it fired about is no longer in the network — the
    only honest way for a finding to go away. ``gate.py`` is not edited, the
    parent's ``gate.json`` is not rewritten, and the sibling faces the same
    unmodified gate on its own merits.

    **The source is evidence and is never edited.** The exclusion is a
    declarative parameter on the new job's record; the copied ``source.osm``
    hashes identically to the parent's.

    **Attribution cannot be forged.** The confirmer is the signed-in session and
    the timestamp is this server's clock, exactly as unit #35's attestation is —
    the body has no field for either. Both, plus the basis sentence verbatim,
    ride into the sibling's skip ledger, so the exclusion is legible from the
    census and the compliance report alone.

    Refusals are NAMED (409 ``{code, reasons}``), never a disabled button. Unit
    #38's four shared doors keep their own codes verbatim
    (``genesis_regenerate_parent_not_gated``, ``..._parent_has_side_inputs``,
    ``..._source_missing``, ``..._source_sha_mismatch``), addressing keeps
    ``genesis_fix_finding_ambiguous_without_subject``, and this path adds
    ``genesis_dead_end_finding_not_in_parent_verdict`` and
    ``genesis_dead_end_not_confirmable`` — the latter quoting the service's own
    measured reason, of which the load-bearing one is
    ``source_testifies_the_connection``: the source declares this connection, and
    a confirmation cannot delete road the map states.
    """
    try:
        record = genesis_jobs.start_dead_end_exclusion_job(
            job_id,
            finding_code=payload.finding_code,
            subject=payload.subject,
            way_id=None,
            basis=payload.basis,
            acknowledged_collateral_way_ids=list(payload.acknowledged_collateral_way_ids),
            confirmed_by=str(user.get("username") or user.get("id") or "unknown"),
        )
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except genesis_jobs.GenesisRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not prepare the re-derivation's work directory. Nothing was started. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    logger.info(
        "genesis: %s confirmed a real-world dead end on job %s (%s / %s) -> job %s",
        user.get("username"),
        job_id,
        payload.finding_code,
        payload.subject,
        record["id"],
    )
    return record


@router.post("/jobs/{job_id}/fixes/apply", status_code=202)
def apply_genesis_fix(
    job_id: str,
    payload: ApplyGenesisFixRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Approve ONE gate-finding fix -> a sibling fix job. 202: the edit is a
    live COM turn and runs on the job queue's worker thread, never here.

    Roadmap B5-2 stage 3. Authenticated the same way ``attach_live_check`` is
    (any signed-in user, no extra role) -- both spend live COM time against a
    project's job tree, and ``approved_by`` on the record is exactly why a
    session is required at all.

    **Two layers, deliberately redundant -- an adversarial pass proved why.**

    1. **This route's own gate**, entirely against the STATIC registry, no
       job touched yet: ``finding_code`` must resolve to a REGISTERED mapping
       (else 400 ``genesis_fix_unknown_finding``), and that mapping must be
       :data:`services.agent.fixes.AUTOMATABLE` and name the EXACT ``verb``
       this request supplies (else 400 ``genesis_fix_verb_mismatch``). Cheap,
       fast-fail, and the common case's whole answer.
    2. **``genesis_jobs.start_genesis_fix_job`` -> ``create_genesis_fix_job``'s
       own gate is the AUTHORITATIVE one.** It re-checks everything layer 1
       does PLUS what layer 1 structurally cannot: unknown parent (404),
       parent not refused (409 ``genesis_fix_parent_not_refused``), the
       (finding_code, subject) does not appear in the PARENT's OWN stored
       verdict (409 ``genesis_fix_finding_not_in_parent_verdict``), the
       finding_code appears on MORE than one subject and the request named
       none (409 ``genesis_fix_finding_ambiguous_without_subject``, listing
       the candidates -- unit #30), the submitted params
       address a DIFFERENT object than the one the finding names (409
       ``genesis_fix_params_target_mismatch``), the fix registry RE-RUN
       against REAL server-side evidence (this workspace's own observation
       sets, unit D4) no longer reaches a matching proposal — most
       importantly a MEASURED entrance (409
       ``genesis_fix_measurement_gate_not_satisfied``) — the submitted
       coordinates differ from the census-derived correction (409
       ``genesis_fix_points_not_census_derived``, unit #34) — or
       ``verb``/``params``
       gone stale (409 ``genesis_fix_verb_no_longer_registered``). ONE call:
       it creates the job record (cloning the parent's artifact, synchronous,
       fast) and starts the COM run on a daemon thread, exactly the split
       ``POST /jobs`` uses for an ordinary generation
       (``genesis_jobs.start_genesis_job``) -- this handler never calls
       ``run_genesis_fix_job`` itself and never touches COM.

    Keep BOTH layers: layer 1 alone is defeatable (a caller that reaches
    layer 2 directly, or a future router bug bypasses layer 1) — an
    executable exploit proved exactly that, twice — while layer 2 alone would
    make every legitimate approval pay for a full re-derivation before the
    caller ever learns their finding_code was a typo. The redundancy is the
    feature, not an oversight.
    """
    mapping = genesis_fixes.get_genesis_fix(payload.finding_code)
    if mapping is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "genesis_fix_unknown_finding",
                "reasons": [
                    f"{payload.finding_code!r} has no registered fix mapping in this catalogue."
                ],
            },
        )
    if mapping.status != agent_fixes.AUTOMATABLE or mapping.verb != payload.verb:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "genesis_fix_verb_mismatch",
                "reasons": [
                    f"Finding {payload.finding_code!r}'s registered mapping is "
                    f"{mapping.status!r}"
                    + (
                        f" naming verb {mapping.verb!r}"
                        if mapping.status == agent_fixes.AUTOMATABLE
                        else ""
                    )
                    + f", not verb {payload.verb!r}. A fix can only be applied through the "
                    "exact verb this catalogue proposed for this finding."
                ],
            },
        )
    try:
        record = genesis_jobs.start_genesis_fix_job(
            job_id,
            finding_code=payload.finding_code,
            subject=payload.subject,
            verb=payload.verb,
            params=dict(payload.params),
            approved_by=str(user.get("username") or user.get("id") or "unknown"),
            approval_rationale=payload.approval_rationale,
            attestation=(
                payload.attestation.model_dump() if payload.attestation is not None else None
            ),
        )
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except genesis_jobs.GenesisRefused as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc
    except (OSError, ValueError) as exc:
        # The same shape ``GET .../gate`` uses for an unreadable stored
        # verdict: create_genesis_fix_job's re-derivation (layer 2 above)
        # reads the parent's gate.json, and a corrupted/unreadable file is a
        # different answer from "no such job" or "refused by name".
        raise HTTPException(
            status_code=409,
            detail=(
                f"The parent's stored gate result could not be read. Nothing was started. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    logger.info(
        "genesis: %s approved fix %s/%s on job %s -> sibling %s",
        user.get("username"),
        payload.finding_code,
        payload.verb,
        job_id,
        record["id"],
    )
    return record


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


@router.get("/jobs/{job_id}/artifact/geojson")
def get_artifact_geojson(job_id: str, user: dict[str, Any] = Depends(current_user)):
    """This generation's draft network as WGS84 GeoJSON — links and connectors.

    U-1-map (docs/ux-backlog.md item #1). The findings map used to draw the
    draft's raw local metres into a bare SVG with no basemap. This route hands
    it degrees instead, converted SERVER-SIDE by
    ``services.genesis.geojson.draft_to_geojson`` on the very plane the draft
    was built on (``metadata.projection``'s recorded origin, inverted through
    ``services.genesis.project.LocalTangentPlane.inverse``, which is the exact
    algebraic inverse of the forward that made those metres).

    **Declared ABOVE ``download_artifact``, and it has to be.** That route's
    path is ``/artifact/{kind}`` and FastAPI matches in declaration order, so
    with this one second every request here would fall through to it and 404 on
    an unknown kind. This is deliberately not a sixth entry in
    ``_ARTIFACT_MEDIA_TYPES`` either: those kinds resolve to a FILE this job
    wrote, and no file on disk holds this payload — it is computed per request.

    Authenticated for exactly the reason ``download_artifact`` is: this is the
    network's geometry, and the adversarial case there (an anonymous caller
    laundering a refused network back in through the un-gated upload path)
    applies to degrees as much as to metres. Workspace scoping is
    ``genesis_jobs.artifact_path``'s: another project's job is a 404, never a
    peek.

    ``no-store``, because a re-gate can revoke a publication while the job id
    stays put and a cached network under a superseded verdict is the one thing
    this screen must not show.
    """
    try:
        path = genesis_jobs.artifact_path(job_id, "genesis_json")
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    import json as _json

    try:
        draft = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This generation's draft could not be read. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc

    try:
        payload = genesis_geojson.draft_to_geojson(draft)
    except genesis_geojson.DraftGeoJsonRefused as exc:
        # 409, not 404: the artifact EXISTS and was read — what it does not
        # carry is the recorded origin, which is a different answer from "no
        # such generation" and calls for a different sentence on screen.
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "reasons": [exc.reason]}
        ) from exc

    return JSONResponse(content=payload, headers={"Cache-Control": "private, no-store"})


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
    filename = path.name
    if kind == "source_osm":
        # On disk this is frozen under a fixed internal name (``source.osm``,
        # ``services/genesis_jobs.py``'s ``frozen_osm``) so it can never collide
        # with a side input in the same work dir. That name means nothing to the
        # caller who uploaded it — the original upload filename is the honest
        # one to hand back, the same way ``artifact_path`` already resolves this
        # kind by re-deriving from the job record rather than trusting a path.
        source = genesis_jobs.get_job(job_id).get("source_osm") or {}
        filename = str(source.get("filename") or filename)
    return FileResponse(
        path, media_type=_ARTIFACT_MEDIA_TYPES[kind], filename=filename
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
# Fix dispositions (roadmap B5-2 stage 3) — "거부는 진단" made clickable: what
# the registry (services.agent.genesis_fixes) says about EVERY finding in a
# job's STORED gate verdict, and the one door that turns an approved entry
# into a sibling fix job.
# ---------------------------------------------------------------------------


#: For an AUTOMATABLE mapping whose builder could not reach a full PROPOSAL
#: from this route's own derived evidence, which ``param_fields`` are
#: genuinely a human's to supply -- and what this route can still fill in
#: from the finding itself while it waits for them. Presentation only, over
#: ``genesis_fixes.VERB_ADDRESSING_FIELD``/``expected_fix_target`` (the
#: SERVICE layer's own addressing map -- see roadmap B5-2 stage-3 fix round):
#: never a second copy of any builder's doctrine, and never invented for a
#: STRUCTURAL refusal (see ``_render_fix_disposition``, which only calls this
#: for the ask-first case, ``fixes.EVIDENCE_INSUFFICIENT``).
def _partial_fix_params(mapping: Any, finding: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    code = finding["code"]
    context = finding["context"]
    addressing_field = genesis_fixes.VERB_ADDRESSING_FIELD.get(mapping.verb)
    expected_target = genesis_fixes.expected_fix_target(code, finding)
    params: dict[str, Any] = {}
    requires: list[str] = list(mapping.param_fields)
    if addressing_field and expected_target is not None:
        params[addressing_field] = expected_target
        if addressing_field in requires:
            requires.remove(addressing_field)
    if code == "v4_more_mapped_lanes_than_link_has":
        mapped_lanes = context.get("mapped_lanes")
        if mapped_lanes is not None:
            params["lane_count"] = int(mapped_lanes)
            requires = [field for field in requires if field != "lane_count"]
    elif code in ("v6_input_without_volume", "v6_input_volume_is_zero"):
        params["interval_no"] = 1
        requires = [field for field in requires if field != "interval_no"]
    elif code == "v9_stop_line_without_queue_counter" and addressing_field in params:
        params["name"] = f"V9 stop line {params[addressing_field]}"
        requires = [field for field in requires if field != "name"]
    return params, requires


def _render_fix_disposition(
    *, check_id: str, origin: str, finding: dict[str, Any], proposal: Any
) -> dict[str, Any]:
    """One finding + the registry's answer for it -> one response entry.

    Three shapes, never a fourth:

    * a real :data:`agent_fixes.PROPOSAL` -- ``verb``/``params`` filled from
      the evidence this route actually had, ``requires`` empty;
    * a declared :data:`agent_fixes.AUTOMATABLE` mapping whose builder
      refused for a MISSING ask-first value (``reason_code ==
      fixes.EVIDENCE_INSUFFICIENT``) -- ``verb`` named, ``params`` filled
      where this route COULD derive them, ``requires`` naming what a caller
      must still supply;
    * everything else -- a declared :data:`agent_fixes.NOT_AUTOMATABLE`
      mapping, a drifted :data:`agent_fixes.VERB_UNREGISTERED`, no mapping at
      all, OR (roadmap B5-2 stage-3 fix round item 3) an AUTOMATABLE mapping
      whose builder refused for a STRUCTURAL reason evidence this route
      itself derived proved true -- most importantly a MEASURED entrance
      (``fixes.INPUT_VOLUME_IS_OBSERVED_DATA``). No amount of caller-supplied
      params would change that verdict, so ``requires`` stays empty and the
      registry's OWN reason code/rationale (already carrying the real
      evidence, via ``genesis_jobs.derive_fix_targets``'s V6 node-evidence
      derivation) tell the true story instead of reading as "just needs a
      volume".

    A FOURTH shape since unit #38, and the only one that is neither an edit nor
    a refusal: :data:`genesis_fixes.REGENERATE_WITH_FIX`, when the refusal's own
    measured reason says the DRAFT already shares the defect
    (``genesis_fixes.regeneration_disposition``). The treatment is a new
    generation re-derived from the same source through the corrected converter
    -- ``POST /jobs/{id}/regenerate`` -- and the entry carries the converter-fix
    ledger and the localized explanation so the card can say what the button
    does. Deliberately a STATUS change rather than an enrichment: "there is no
    automated fix" would be false about a finding that has a treatment.

    Optional enrichments inside the third shape, never a fifth shape:
    a V4 refusal whose evidence carries ``boundary_attribution`` (unit #39) and
    -- when that attribution earned one -- ``boundary_extension`` (unit #40,
    the wider lasso that would close the measured shortage, an ask on ANOTHER
    screen rather than an action here);
    a V3 refusal whose evidence carries ``source_testimony`` (unit #36 -- the
    job's own source ``.osm`` declares the connection the network lost) and
    one whose evidence carries ``attestation_invitation`` (unit #35 -- the
    source shares the gap, but this model holds link ends a human could attest
    the road continues to, each with the measured distance). Both surface
    verbatim, key-ABSENT otherwise, and neither changes the tier: a testified
    V3 silent-departure finding reaches the FIRST shape on its own (a real
    PROPOSAL naming ``add_connector``), and a world-gap one stays a refusal
    until somebody attests through ``POST .../fixes/apply``.
    And, since unit #42, a V3 world-gap refusal also carries
    ``dead_end_confirmation`` -- the OTHER thing it may be, a road that really
    ENDS -- naming the one way a confirmation would exclude and what the source
    says would come loose with it; or ``dead_end_refusal``, naming why that
    disposition is unavailable (``source_testifies_the_connection`` above all:
    a human confirmation may not delete road the source declares). Same rules as
    every enrichment -- key-ABSENT otherwise, no tier change -- and the action is
    a POST to ``.../dead-end-confirmation`` that starts a NEW re-derivation
    without the confirmed way, never an edit to this artifact.
    """
    mapping = genesis_fixes.get_genesis_fix(finding["code"])
    disposition: dict[str, Any] = {
        "status": agent_fixes.NOT_AUTOMATABLE,
        "verb": None,
        "params": None,
        "requires": [],
        "confidence": proposal.confidence,
        "assumptions": list(proposal.assumptions),
        "rationale": proposal.rationale(),
        "reason_code": proposal.reason_code,
    }
    if proposal.outcome == agent_fixes.PROPOSAL:
        disposition["status"] = agent_fixes.AUTOMATABLE
        disposition["verb"] = proposal.verb
        disposition["params"] = proposal.params
    elif mapping is not None and mapping.status == agent_fixes.AUTOMATABLE:
        status, _detail = genesis_fixes.effective_status(mapping)
        disposition["status"] = status
        disposition["verb"] = mapping.verb
        if status == agent_fixes.AUTOMATABLE and proposal.reason_code == agent_fixes.EVIDENCE_INSUFFICIENT:
            params, requires = _partial_fix_params(mapping, finding)
            disposition["params"] = params
            disposition["requires"] = requires
    # Unit #36: when the refusal's own evidence carries the SOURCE's testimony
    # (a V3 finding whose job's source .osm declares the connection the
    # network lost -- genesis_jobs.derive_v3_source_testimony), surface it on
    # the disposition so a reviewer or a regeneration consumes the measured
    # ids, not just the rationale prose. Key-ABSENT everywhere else (presence
    # is the fact), and never a tier change -- the entry stays NOT_AUTOMATABLE.
    testimony = (proposal.evidence or {}).get("source_testimony")
    if isinstance(testimony, dict):
        disposition["source_testimony"] = testimony
    # Unit #39: when the V4 refusal's own evidence carries a MEASURED boundary
    # attribution (the runway corridor's upstream end classified against the
    # census's own boundary markers and the source's declared continuation --
    # genesis_jobs.derive_v4_boundary_attribution), surface it on the
    # disposition so a reviewer or unit #40's boundary-extension proposal
    # consumes the measured ids and metres, not just the rationale prose.
    # Key-ABSENT everywhere else (presence is the fact), and never a tier
    # change -- the entry stays a refusal whichever way the attribution lands.
    attribution = (proposal.evidence or {}).get("boundary_attribution")
    if isinstance(attribution, dict):
        disposition["boundary_attribution"] = attribution
    # Unit #40: the TREATMENT that attribution earns -- the user's own
    # requested lasso with a finger spliced into the edge the corridor
    # crosses, long enough to cover the measured deficit plus its margin
    # (genesis_jobs.derive_v4_boundary_extension). Key-ABSENT when no
    # extension could be derived (the named reason is in the rationale
    # instead), and NOT a tier change: the entry stays a refusal, because
    # widening the region is a NEW GENERATION a human starts on the region
    # screen -- never an edit to this artifact, and never unit #38's
    # same-source regeneration, whose bytes are clipped at this very boundary.
    #
    # UNIT #48-b (user decision, 2026-08-20): NO CLIENT RENDERS THIS KEY'S
    # DERIVED SHAPE ANY MORE. The widen-and-regenerate buttons it fed were
    # removed once live use showed that every widened ring cuts new roads at its
    # own new edge. The key is still sent, and the derivation behind it is
    # retained as the basis for a future PRE-FLIGHT boundary check on the region
    # screen (see ``genesis_jobs.derive_v4_boundary_extension``'s own note). What
    # the screen offers instead is a manual ``영역 편집`` button carrying the
    # job's own requested polygon.
    #
    # ITS PRESENCE IS READ, THOUGH -- corrected by the refuter, because this
    # comment first said "no client renders this key" while one branch of
    # ``app/genesis/jobs/[id]/page.tsx`` was testing it, which is how a stale
    # imperative ("경계를 넓혀 다시 생성해야 합니다") survived the removal
    # unnoticed. That branch asks only whether the key is THERE, to decide
    # whether a NOT_AUTOMATABLE card names the boundary as the cause and points
    # at 영역 편집. It is the right question: this key is absent unless the census
    # recorded a requested POLYGON, which is the same field that button needs
    # before it renders, so the sentence can only name a control that exists.
    # None of the measurements inside are read.
    extension = (proposal.evidence or {}).get("boundary_extension")
    if isinstance(extension, dict):
        disposition["boundary_extension"] = extension
    # Unit #48: the SAME two keys for the OTHER finding the requested boundary
    # can cause -- a ``v3_orphan_link`` whose way the lasso cut at one or both
    # ends (``genesis_jobs.derive_v3_orphan_boundary_attribution``), and the
    # region that would undo it, one finger per SEVERED END
    # (``derive_boundary_extension_for_findings`` restricted to this finding).
    # Deliberately its own key rather than #40's: an orphan cut at both ends
    # needs TWO fingers, which ``boundary_extension``'s single ``corridor_patch``
    # cannot carry, and widening that payload would have made every existing
    # reader guess whether a second patch existed. Same rules as every
    # enrichment -- key-ABSENT otherwise, presence is the fact, and no tier
    # change: the entry stays a refusal under CONNECTIVITY_IS_NOT_INVENTED,
    # because the treatment is a new generation a human asks for on the region
    # screen, never an edit to this artifact. An orphan the SOURCE itself
    # leaves unconnected carries the attribution's named refusal and NO region.
    orphan_attribution = (proposal.evidence or {}).get("orphan_boundary_attribution")
    if isinstance(orphan_attribution, dict):
        disposition["orphan_boundary_attribution"] = orphan_attribution
    # ``orphan_boundary_attribution`` is READ by the client again since unit
    # #48-b: it is what the 영역 편집 hand-off quotes to tell a reader WHICH way
    # the ring cut and at WHICH node. ``boundary_extension_patches`` is the
    # derived-region half and is un-exposed with the rest of it -- same decision,
    # same reason, same retention (see ``_render_fix_disposition``'s note on
    # ``boundary_extension`` above).
    orphan_extension = (proposal.evidence or {}).get("boundary_extension_patches")
    if isinstance(orphan_extension, dict):
        disposition["boundary_extension_patches"] = orphan_extension
    # Unit #35: when the refusal is a world gap the model COULD still answer --
    # the source shares the gap, but this network holds link ends a connector
    # could reach -- carry the ask-first question's own data: which link ends
    # a human may attest the road continues to, each with the MEASURED metres
    # between the two ends. Key-ABSENT everywhere else (presence is the fact),
    # and never a tier change: the entry stays a refusal until somebody
    # attests, and an attestation is a POST, not a rendering.
    invitation = (proposal.evidence or {}).get("attestation_invitation")
    if isinstance(invitation, dict):
        disposition["attestation"] = invitation
    # Unit #42: the SECOND ask-first question on the same refusal -- the road may
    # really END here. Present only when the source shares the gap and this
    # module's own derivation reached an offer; it names the ONE way a
    # confirmation would exclude, the census links carrying it, and what the
    # source says would come loose with it, so the reader signs for a measured
    # scope rather than a button. Never a tier change: confirming is a POST that
    # starts a NEW re-derivation, and nothing about this artifact is edited.
    dead_end = (proposal.evidence or {}).get("dead_end_confirmation")
    if isinstance(dead_end, dict):
        disposition["dead_end_confirmation"] = dead_end
    # ... and when it is NOT on offer for a reason a reader deserves named --
    # above all ``source_testifies_the_connection``, the evidence hierarchy that
    # keeps a human confirmation from deleting road the source declares -- the
    # measured reason surfaces as DATA beside the prose that already carries it.
    # A refusal, never a control: this key renders a sentence, not a button.
    dead_end_refusal = (proposal.evidence or {}).get("dead_end_refusal")
    if isinstance(dead_end_refusal, dict):
        disposition["dead_end_refusal"] = dead_end_refusal
    # Unit #38: the THIRD disposition. When the refusal's own measurement says
    # the draft already shares the defect -- so no census-derived edit exists
    # and no coordinate may be invented -- the honest treatment is a
    # re-derivation from the same source through the corrected converter, and
    # the entry says so instead of ending at "no automated fix". Tier CHANGES
    # here (uniquely among the enrichments above): this is a different kind of
    # answer, not a footnote on a refusal, and the button it renders posts to
    # ``POST /jobs/{id}/regenerate`` -- whose own eligibility gate re-derives
    # this very measurement, so the two cannot disagree.
    regenerate = genesis_fixes.regeneration_disposition(
        finding["code"], proposal, converter_fixes=genesis_osm.CONVERTER_FIXES
    )
    if regenerate is not None:
        disposition["status"] = genesis_fixes.REGENERATE_WITH_FIX
        # The registered verb is CLEARED, not kept beside the new status. The
        # mapping still names ``set_link_geometry`` -- it is the right verb for
        # a v7 finding the census CAN correct -- but for this one the
        # measurement says no census-derived params exist, so leaving the verb
        # named would invite a client to build an apply request that
        # ``create_genesis_fix_job`` then refuses. This finding's treatment is
        # not an edit, and the entry should not read as if it half were.
        disposition["verb"] = None
        disposition["params"] = None
        disposition["requires"] = []
        disposition["regenerate"] = regenerate
    return {
        "check_id": check_id,
        "origin": "warning" if origin == "warnings" else "finding",
        "code": finding["code"],
        "subject": finding["subject"],
        "context": finding["context"],
        "disposition": disposition,
    }


@router.get("/jobs/{job_id}/fixes")
def get_job_fixes(job_id: str, user: dict[str, Any] = Depends(current_user)):
    """Every finding in this job's STORED gate verdict, with the registry's
    disposition for each. Roadmap B5-2 stage 3.

    Authenticated like the artifact/geojson reads: this exposes the gate's own
    diagnosis of a network, the same laundering argument ``download_artifact``
    names. 404 unknown job; 409 ``genesis_fix_no_gate_verdict`` when the job
    exists but has not been gated yet (the job's OWN status, quoted, so a
    caller sees whether to poll or to look elsewhere) -- the same distinction
    ``GET .../gate`` draws between "no such job" and "no verdict yet".

    Served from the STORED ``gate.json`` (never re-run), and read RAW rather
    than through ``genesis_jobs.gate_payload`` -- that render drops each
    finding's ``context`` and caps the list at
    ``genesis_jobs.MAX_FINDINGS_IN_PAYLOAD``, and this route needs the full
    context every builder reads AND every finding with no drop ("complete
    over the verdict's findings" is the whole point of the unit).
    ``genesis_jobs.iter_stored_gate_findings`` is the ONE walk this route and
    ``create_genesis_fix_job``'s own finding lookup share (both a check's
    ``findings`` and its ``warnings`` -- V6 rides its own warn-tier clause on
    a refuse-tier check as ``warnings``, ``services.genesis.gate``'s own
    module docstring -- and a finding living there is still a finding this
    catalogue must answer for). This route is deliberately UNCAPPED, unlike
    ``gate_payload``: measured on a synthetic 1001-finding fixture, the full
    response is ~848 KB and renders in ~0.094s -- large, not pathological,
    and a truncated fix list would silently hide exactly the findings a
    reviewer most needs to see.

    Evidence (census + V6/V9 targets) comes from
    ``genesis_jobs.job_draft_or_none``/``derive_fix_targets`` -- the SAME
    service-layer functions ``create_genesis_fix_job`` re-derives a proposal
    with before minting a sibling job, so a GET here and a POST .../apply
    right after it can never disagree about what the evidence says.
    ``params=None`` here (never a caller-supplied volume/geometry -- this is
    a GET), but V6's MEASUREMENT evidence still flows unconditionally, so a
    measured entrance's disposition shows the real structural refusal rather
    than always reading as "just needs a volume" (fix round item 3). V7's
    geometry evidence flows the same way since unit #34
    (``derive_v7_corrected_polyline``, keyed by ``job_id``): a draft-
    correctable polyline finding renders as a full PROPOSAL carrying the
    draft's own points, and an uncorrectable one keeps the
    ``NO_TOOL_WITHOUT_FABRICATION`` refusal with the measured reason folded
    into its rationale. And V3's source testimony flows the same way since
    unit #36 (``derive_v3_source_testimony``): every connectivity refusal's
    rationale states, measured from the job's own source ``.osm``, whether
    the source shares the gap or declares the connection the pipeline lost --
    the tier stays NOT_AUTOMATABLE either way.

    ``unmapped_finding_codes`` is the registry's own mirror check
    (:func:`genesis_fixes.get_unmapped_finding_codes`) -- every code
    ``gate.py`` can emit either resolves in the catalogue or is named here as
    a gap, so a client can see "no code fell between" without re-deriving the
    check itself.
    """
    try:
        record = genesis_jobs.get_job(job_id)
    except genesis_jobs.GenesisJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    stored = genesis_jobs.job_dir(job_id) / genesis_jobs.GATE_JSON_FILENAME
    if not stored.is_file():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "genesis_fix_no_gate_verdict",
                "reasons": [
                    f"Job {job_id} has not been gated yet (status: {record.get('status')})."
                ],
            },
        )

    import json as _json

    try:
        raw = _json.loads(stored.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"The stored gate result could not be read. [{type(exc).__name__}: {exc}]",
        ) from exc

    draft = genesis_jobs.job_draft_or_none(job_id)
    census = draft.metadata if draft is not None else None

    entries: list[dict[str, Any]] = []
    # Unit #48: kept beside `entries` so the job-level composite below consumes
    # the SAME derivations each card was rendered from -- a second pass over the
    # findings could disagree with the first, and two shapes proposed for one
    # job under two buttons is the defect this unit exists to close.
    derived: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for check_id, origin, finding in genesis_jobs.iter_stored_gate_findings(raw):
        targets = genesis_jobs.derive_fix_targets(
            finding["code"], finding, draft=draft, params=None, job_id=job_id
        )
        derived.append((finding, targets))
        proposal = genesis_fixes.propose_genesis_fix(
            finding["code"], finding=finding, census=census, targets=targets
        )
        entries.append(
            _render_fix_disposition(check_id=check_id, origin=origin, finding=finding, proposal=proposal)
        )

    payload: dict[str, Any] = {
        "job_id": job_id,
        "status": record.get("status"),
        "verdict": raw.get("verdict"),
        "findings": entries,
        "unmapped_finding_codes": list(genesis_fixes.get_unmapped_finding_codes()),
    }
    # Unit #48 -- ONE lasso for EVERY boundary-caused finding in this verdict.
    #
    # THE DEFECT IT CLOSES, measured on the user's own 2026-08-19 demo: a job
    # with a boundary-caused runway shortage AND a boundary-severed orphan
    # offered two separate widened regions, each derived from the same original
    # polygon, so taking one discarded the other -- and since the two fingers
    # open onto different edges of the ring, repeating the extension never
    # healed the orphan. This key is the region that covers all of them at once.
    #
    # Key-ABSENT when nothing earned one; a REFUSAL is carried under its own key
    # (two fingers colliding on one edge, a corridor whose finger cannot be
    # drawn, the area cap) so the screen can NAME why the all-at-once option is
    # missing instead of going quiet -- and the per-finding proposals above are
    # unaffected either way, which is exactly what makes refusing here safe.
    combined = genesis_jobs.derive_boundary_extension_for_findings(
        derived, draft=draft, job_id=job_id
    )
    if "reason_code" in combined:
        # `no_boundary_findings` is the ordinary case for a job with no boundary
        # story at all, and a refusal key on every such response would be noise
        # rather than a disclosure -- the cards themselves already say nothing
        # about a boundary.
        if combined.get("reason_code") != "no_boundary_findings":
            payload["boundary_extension_all_refusal"] = combined
    else:
        payload["boundary_extension_all"] = combined
    return payload


# ---------------------------------------------------------------------------
# Region → .osm extraction (roadmap v2 §5.B row B2)
# ---------------------------------------------------------------------------


class StartRegionFetchRequest(BaseModel):
    """ONE region shape — a bounding box or a polygon — plus an optional name.

    ``extra="forbid"``, and the endpoint is NOT in this model on purpose: the
    Overpass instance is server configuration (``PRISM_OVERPASS_ENDPOINT``), and
    a caller-supplied URL would turn this route into an SSRF instrument pointed
    at whatever the server can reach.

    **Exactly one shape, enforced here rather than downstream.** A polygon's
    bounding box is DERIVED from the polygon (``osm_fetch.RegionPolygon``), so a
    request carrying both would be two claims about one region — and the pair
    that disagrees is the pair that fetches one area and judges its boundary
    against another. A request carrying neither is a caller who forgot the
    region, not a caller asking for the whole planet.
    """

    model_config = ConfigDict(extra="forbid")

    south: float | None = Field(default=None, description="Southern latitude, WGS84 degrees.")
    west: float | None = Field(default=None, description="Western longitude, WGS84 degrees.")
    north: float | None = Field(default=None, description="Northern latitude, WGS84 degrees.")
    east: float | None = Field(default=None, description="Eastern longitude, WGS84 degrees.")
    polygon: list[list[float]] | None = Field(
        default=None,
        description=(
            "A lasso, as [[lat, lon], …] in WGS84 degrees. The ring may be sent open or "
            "closed; at least 3 and at most 64 corners; self-intersecting, degenerate and "
            "antimeridian-spanning rings are refused by name."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Base name for the staged extract. Omitted = derived from the centre.",
    )

    def region(self) -> osm_fetch.RequestedRegion:
        """The one shape this request names, validated by its own class.

        The exactly-one rule is enforced HERE and raised as an
        ``OsmFetchRefused``, not as a pydantic validator: a validator would answer
        422 with pydantic's own error list, and every other refusal on this route
        answers 400 with ``{code, reasons, measurement}``. One refusal shape means
        the browser has one renderer, which is the whole reason
        :func:`_region_refusal` exists.
        """
        corners = (self.south, self.west, self.north, self.east)
        has_box = all(value is not None for value in corners)
        if any(value is not None for value in corners) and not has_box:
            missing = [
                key
                for key, value in zip(("south", "west", "north", "east"), corners)
                if value is None
            ]
            raise osm_fetch.OsmFetchRefused(
                "region_shape_ambiguous",
                [
                    "A bounding box needs all four of south/west/north/east; missing: "
                    + ", ".join(missing)
                    + ". Send a polygon instead if the region is not a rectangle."
                ],
            )
        if has_box == (self.polygon is not None):
            raise osm_fetch.OsmFetchRefused(
                "region_shape_ambiguous",
                [
                    "Send exactly one region shape: south/west/north/east, or polygon — "
                    "never both and never neither. A polygon's bounding box is derived "
                    "from the polygon, so sending one as well would be two claims about "
                    "the same region."
                ],
            )
        if self.polygon is not None:
            return osm_fetch.RegionPolygon.from_sequence(self.polygon)
        return osm_fetch.BoundingBox(
            south=float(self.south),  # type: ignore[arg-type]
            west=float(self.west),  # type: ignore[arg-type]
            north=float(self.north),  # type: ignore[arg-type]
            east=float(self.east),  # type: ignore[arg-type]
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


#: How a polygon travels on the PREVIEW's query string: ``lat,lon`` pairs joined
#: by ``;``. The preview is a GET (open, cacheable, no side effect) and it stays
#: one, so the shape has to fit in a URL — 64 corners at 7 decimals is ~1.4 kB,
#: well inside every server's line limit. Refusals name this format literally so
#: a caller who guessed JSON is told what to send instead.
_POLYGON_QUERY_FORMAT = 'polygon=lat,lon;lat,lon;lat,lon (at least three pairs)'


def _polygon_from_query(raw: str) -> osm_fetch.RegionPolygon:
    pairs: list[list[float]] = []
    for index, chunk in enumerate(part.strip() for part in raw.split(";")):
        if not chunk:
            continue
        fields = [value.strip() for value in chunk.split(",")]
        if len(fields) != 2:
            raise osm_fetch.OsmFetchRefused(
                "region_polygon_invalid",
                [f"Corner {index + 1} ({chunk!r}) is not a lat,lon pair. Use {_POLYGON_QUERY_FORMAT}."],
            )
        try:
            pairs.append([float(fields[0]), float(fields[1])])
        except ValueError as exc:
            raise osm_fetch.OsmFetchRefused(
                "region_polygon_invalid",
                [f"Corner {index + 1} ({chunk!r}) is not numeric ({exc}). Use {_POLYGON_QUERY_FORMAT}."],
            ) from exc
    return osm_fetch.RegionPolygon.from_sequence(pairs)


@router.get("/region/preview")
def preview_region(
    south: float | None = None,
    west: float | None = None,
    north: float | None = None,
    east: float | None = None,
    polygon: str | None = None,
):
    """Measure a region against cap 1 WITHOUT fetching anything.

    This is what lets a browser show the area and the estimated size against the
    limit before a byte is downloaded. It answers 200 with
    ``within_region_cap: false`` for a region that is too large rather than
    400-ing: a preview whose whole job is to describe an over-large box must be
    able to describe an over-large box.

    Same exactly-one-shape rule as ``POST /region/fetches`` — a preview that
    could measure a different shape than the fetch would end up describing is a
    preview nobody should trust. A polygon arrives as ``lat,lon;lat,lon;…``
    (:data:`_POLYGON_QUERY_FORMAT`); its measurement carries the derived
    rectangle under ``region`` exactly as the fetch's provenance will.
    """
    corners = (south, west, north, east)
    has_box = all(value is not None for value in corners)
    if has_box == (polygon is not None):
        raise _region_refusal(
            osm_fetch.OsmFetchRefused(
                "region_shape_ambiguous",
                [
                    "Ask for exactly one shape: south/west/north/east, or "
                    f"{_POLYGON_QUERY_FORMAT} — never both and never neither."
                ],
            )
        )
    try:
        region: osm_fetch.RequestedRegion = (
            _polygon_from_query(polygon)
            if polygon is not None
            else osm_fetch.BoundingBox(
                south=float(south),  # type: ignore[arg-type]
                west=float(west),  # type: ignore[arg-type]
                north=float(north),  # type: ignore[arg-type]
                east=float(east),  # type: ignore[arg-type]
            )
        )
    except osm_fetch.OsmFetchRefused as exc:
        raise _region_refusal(exc) from exc
    return osm_fetch.measure_region(region).as_dict()


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
        region = payload.region()
        polygon = osm_fetch.region_polygon(region)
        record = osm_fetch.start_region_fetch(
            # Exactly one of the two reaches the service, decided by what the
            # caller drew. A polygon's rectangle is derived INSIDE the service so
            # that this router cannot become a second place it is computed.
            bbox=None if polygon is not None else osm_fetch.region_bbox(region),
            polygon=polygon,
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
    image_sha256: str | None = Field(
        default=None,
        description=(
            "The image the human actually looked at, or null for a review that HAS no image "
            "(unit #52's 엑셀 업로드 / 직접 입력). Checked against the stored render either way: "
            "claiming an image this review does not hold is refused, and so is dropping one it does."
        ),
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


class AmendPhaseDiagramTimingRequest(BaseModel):
    """ONE 신호 시간 value a person corrected on the card (unit #58).

    ``edited_by`` and ``edited_at`` are NOT here, for exactly the reason
    :class:`ConfirmPhaseDiagramRequest` carries no ``attested_by``: the stamp is
    a claim about who said what and when, and the cheapest way to stop a caller
    putting somebody else's name on it is to give them no field to put it in.
    The server takes the editor from the session and the time from its own clock.

    ``field`` is a closed vocabulary, built from the store's own three sets so
    the route and the writer cannot drift apart. One field per request, because
    the ledger records ``was → now`` per value and a batch would have to invent
    an order for its own history.
    """

    model_config = ConfigDict(extra="forbid")

    field: phase_diagram.AmendableField = Field(
        description="Which value. 'declared_amber_s' is review-scoped; the rest name a plan."
    )
    level_no: int | None = Field(
        default=None, description="Required for every field except declared_amber_s."
    )
    plan_id: int | None = None
    phase_id: str | None = Field(
        default=None, max_length=64, description="Required for the per-현시 fields."
    )
    #: The typed value, verbatim. ``None`` clears an optional field; the store
    #: refuses it by name where the field may not be empty (green, 소거, amber).
    value: float | str | None = None


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


# ---------------------------------------------------------------------------
# unit #52 — 현시도 입력: a template out, a workbook in, a blank one made here
# ---------------------------------------------------------------------------
#
# Three ways INTO the same review store, and all three produce the same thing: a
# PROPOSAL (or an empty form), never a fact. Nothing below writes an attestation,
# and there is no argument on any of these routes that could make one — the only
# road to a confirmed mapping is still POST /phase-diagrams/{id}/confirmation
# with the attester taken from the session.
#
# These are declared ABOVE ``GET /phase-diagrams/{review_id}`` on purpose:
# FastAPI matches in declaration order, so a static path added below it would be
# swallowed by the id parameter and answer 404 for a file that exists.


class CreateBlankPhaseDiagramRequest(BaseModel):
    """Make an empty review for one named intersection (roadmap C1 option (b),
    reachable from the screen at last).

    There is no ``phases`` field and no proposal: this route creates the BLANK
    FORM, and everything in it is typed by the human who then confirms it. The
    name is required and must not be blank — a review nobody can identify is one
    a reviewer cannot bind to a node.
    """

    model_config = ConfigDict(extra="forbid")

    intersection_name: str = Field(min_length=1, max_length=200)
    intersection_no: str | None = Field(default=None, max_length=64)
    #: Where the creation was started from, recorded verbatim as PROVENANCE —
    #: e.g. "네트워크 도식 노드 12". Never a binding: the node a mapping belongs to
    #: is chosen on the confirm form, against a generation's own draft.
    origin_note: str | None = Field(default=None, max_length=200)


def _xlsx_response(content: bytes, filename: str) -> Response:
    """One .xlsx download, named for a human and not cached.

    ``Content-Disposition`` carries both the ASCII fallback and the UTF-8 form
    (RFC 5987), because every intersection name in this product is Korean and a
    browser that only reads the plain ``filename=`` would otherwise save mojibake.
    """
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("_") or "phase-diagram.xlsx"
    quoted = quote(filename, safe="")
    return Response(
        content=content,
        media_type=phase_template.XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}",
            "Cache-Control": "no-store",
        },
    )


@router.get("/phase-diagrams/template.xlsx")
def download_phase_diagram_template(user: dict[str, Any] = Depends(current_user)):
    """The standard 현시도 input template: 안내 + 현시입력 + one example row.

    The 안내 sheet states the doctrine in the file itself, because the file
    travels: somebody will fill this in a week from now, in an email thread, with
    nothing else from this app on screen. It has to say that what comes back is a
    proposal and that a human confirms it here.
    """
    return _xlsx_response(phase_template.build_template(), phase_template.template_filename())


@router.post("/phase-diagrams/upload")
async def upload_phase_diagram_template(
    request: Request,
    x_filename: str | None = Header(None),
    user: dict[str, Any] = Depends(current_user),
):
    """One filled-in template → one PROPOSAL per 교차로명 in it.

    Raw body + ``X-Filename``/``?filename=``, the two-step shape
    ``routers/observations.py`` documents (this backend carries no
    ``python-multipart``). ``xlsx_guard`` runs before openpyxl is handed a byte.

    What lands is a review whose ``render`` says ``medium="excel_upload"`` and
    carries NO image — the screen shows a named placeholder where the drawing
    would be. The proposal records who uploaded it as provenance. Nothing is
    confirmed, and a row this parser refused takes its whole intersection with
    it, named, rather than importing a junction with one leg quietly missing.
    """
    filename = Path(unquote(request.query_params.get("filename") or x_filename or "")).name
    if not filename:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "phase_template_no_filename",
                "reasons": [
                    "업로드된 파일의 이름이 없습니다. 검토는 파일 이름과 교차로명으로 식별되므로 "
                    "이름 없는 업로드는 받지 않습니다."
                ],
            },
        )
    _reject_oversized_body(request)
    content = await request.body()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)

    try:
        parsed = phase_template.parse_template(content, source_file=filename)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "reasons": exc.reasons}
        ) from exc

    store = _phase_diagram_store()
    uploader = str(user.get("username") or user.get("id") or "").strip()
    source_sha = phase_diagram.sha256_hex(content)
    created: list[dict[str, Any]] = []
    refusals = parsed.refusal_dicts()

    for entry in parsed.intersections:
        # Keyed on (workbook, 교차로명) so one file's several intersections are
        # several reviews, and a corrected re-upload of the same file lands back
        # in the same ones.
        review_id = phase_diagram.review_id_for(
            phase_diagram.composite_source_key(filename, entry.name)
        )
        existing = store.attestation(review_id)
        if existing is not None:
            # A confirmation is a person's claim. An upload never overwrites one:
            # it would replace a named human's statement with a spreadsheet, and
            # the screen would show the new table under the old attester's name.
            refusals.append(
                {
                    "code": "phase_template_review_confirmed",
                    "reasons": [
                        f"{entry.name}: 이미 {existing.attested_by}님이 확인한 배정이 있어 "
                        "업로드로 덮어쓰지 않았습니다. 화면에서 다시 확인하십시오."
                    ],
                    "row": None,
                    "intersection": entry.name,
                }
            )
            continue
        replaced = False
        try:
            replaced = store.render(review_id) is not None
        except phase_diagram.PhaseDiagramRefused:
            replaced = False
        proposal = entry.to_proposal(source_file=filename, uploaded_by=uploader)
        try:
            store.put_manual_review(
                review_id,
                phase_diagram.manual_render(
                    medium="excel_upload", source_file=filename, source_sha256=source_sha
                ),
                sheet={
                    "intersection_name": entry.name,
                    "intersection_no": entry.intersection_no,
                    "phase_ids": proposal.phase_ids,
                    "plan_choices": [],
                    # The 신호시간 sheet's rows, per (수준, 계획ID). Empty is a
                    # first-class state: the mapping is still confirmable and
                    # the signals build refuses by name until timing exists.
                    "timing": entry.timing_payload(),
                    "declared_amber_s": entry.declared_amber_s,
                    "amber_declaration_note": entry.amber_note,
                    "uploaded_by": uploader,
                    "rows": entry.rows,
                },
            )
            store.put_proposal(
                review_id,
                proposal,
                phase_diagram.cross_check_without_text_layer(medium="excel_upload"),
            )
        except phase_diagram.PhaseDiagramRefused as exc:
            raise _phase_diagram_refusal(exc) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The uploaded 현시도 could not be written. Nothing was stored for it. "
                    f"[{type(exc).__name__}: {exc}]"
                ),
            ) from exc
        created.append(
            {
                "review_id": review_id,
                "intersection_name": entry.name,
                "intersection_no": entry.intersection_no,
                "phase_ids": proposal.phase_ids,
                "declared_amber_s": entry.declared_amber_s,
                "amber_declaration_note": entry.amber_note,
                "plans": [
                    {"level_no": plan["level_no"], "plan_id": plan["plan_id"]}
                    for plan in entry.timing_payload()
                ],
                "replaced": replaced,
            }
        )

    return {
        "source_file": filename,
        "uploaded_by": uploader,
        "created": created,
        "refusals": refusals,
        "skipped_example_rows": parsed.skipped_example_rows,
    }


@router.post("/phase-diagrams/blank", status_code=201)
def create_blank_phase_diagram(
    payload: CreateBlankPhaseDiagramRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """An empty review for one intersection a human names, and nothing else.

    No proposal, no image, no reading — the review opens straight into the blank
    manual-entry form the screen already has for a refused proposal. An existing
    review for the same name is a 409 rather than an overwrite: re-creating would
    silently replace whatever is in it, up to and including a confirmation.
    """
    name = payload.intersection_name.strip()
    if not name:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "phase_diagram_blank_name_required",
                "reasons": [
                    "교차로명을 입력하십시오. 이름 없는 현시도는 나중에 어느 교차로의 것인지 "
                    "아무도 알 수 없습니다."
                ],
            },
        )
    key = phase_diagram.composite_source_key("blank", name)
    review_id = phase_diagram.review_id_for(key)
    store = _phase_diagram_store()
    try:
        store.render(review_id)
    except phase_diagram.PhaseDiagramRefused:
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "phase_diagram_review_exists",
                "reasons": [
                    f"'{name}' 현시도가 이미 있습니다 (검토 {review_id}). 새로 만들지 않고 "
                    "기존 검토를 여십시오 — 덮어쓰면 이미 입력한 내용이 사라집니다."
                ],
            },
        )
    sheet: dict[str, Any] = {
        "intersection_name": name,
        "intersection_no": (payload.intersection_no or "").strip() or None,
        "phase_ids": [],
        "plan_choices": [],
        "created_by": str(user.get("username") or user.get("id") or "").strip(),
    }
    if payload.origin_note:
        # Provenance only. The node a mapping belongs to is bound on the confirm
        # form against a generation's own draft — a note is not a binding.
        sheet["origin_note"] = payload.origin_note.strip()
    try:
        store.put_manual_review(
            review_id,
            phase_diagram.manual_render(
                medium="blank_entry",
                source_file=name,
                source_sha256=phase_diagram.sha256_hex(key.encode("utf-8")),
            ),
            sheet=sheet,
        )
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The blank 현시도 could not be created. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc
    return store.review_payload(review_id)


def _export_entry(
    store: phase_diagram.PhaseDiagramStore, review_id: str
) -> phase_template.ExportEntry:
    """One review as the draft workbook needs it, TIMING INCLUDED.

    Where the timing comes from is the whole point: a 대전 review's comes from
    its own sheet (``daejeon_timing_payload`` re-uses that path's arithmetic, so
    an export→re-upload rebuilds the same program), and an uploaded review's is
    what somebody already typed. A blank review carries none and exports none —
    nothing here fabricates a green.
    """
    render = store.render(review_id)
    summary = store.sheet_summary(review_id)
    name = summary.get("intersection_name") or render.source_file
    plans: list[dict[str, Any]] = []
    notes: list[str] = []
    if render.medium == "pdf_band":
        plans, notes = phase_template.daejeon_timing_payload(store.sheet(review_id))
    else:
        plans = list((store.manual_sheet(review_id) or {}).get("timing") or [])
    return phase_template.ExportEntry(
        intersection_name=str(name),
        intersection_no=summary.get("intersection_no"),
        proposal=store.proposal(review_id),
        plans=plans,
        notes=notes,
        declared_amber_s=summary.get("declared_amber_s"),
        amber_declaration_note=summary.get("amber_declaration_note"),
    )


@router.get("/phase-diagrams/exports.xlsx")
def export_all_phase_diagrams(user: dict[str, Any] = Depends(current_user)):
    """Every review in this workspace as ONE draft workbook, stamped 미확인.

    The file is the same template these routes read back in, so a reviewer can
    correct it in Excel and upload it — the round trip is the point. Every sheet
    carries ``UNCONFIRMED_MARK`` because the file outlives the screen it was
    downloaded from.
    """
    store = _phase_diagram_store()
    entries = []
    for review_id in store.review_ids():
        try:
            entries.append(_export_entry(store, review_id))
        except phase_diagram.PhaseDiagramRefused:
            continue
    if not entries:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "phase_diagram_nothing_to_export",
                "reasons": ["내보낼 현시도 검토가 없습니다."],
            },
        )
    return _xlsx_response(
        phase_template.build_export(entries), "현시도-제안-미확인-전체.xlsx"
    )


@router.get("/phase-diagrams/{review_id}/export.xlsx")
def export_phase_diagram(review_id: str, user: dict[str, Any] = Depends(current_user)):
    """One review's proposal as a filled-in draft template, stamped 미확인."""
    store = _phase_diagram_store()
    try:
        entry = _export_entry(store, review_id)
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    return _xlsx_response(
        phase_template.build_export([entry]),
        f"{entry.intersection_name}-현시도-제안-미확인.xlsx",
    )


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
    candidates = phase_diagram.node_candidates(draft, intersection_name=sheet.intersection_name)
    return {
        "review_id": review_id,
        "job_id": job_id,
        "intersection_name": sheet.intersection_name,
        "candidates": candidates,
        # unit #62 -- ADDITIVE disclosure fields. `node_candidates` truncates
        # ONLY when its name ranking actually discriminates, but even then a
        # silent cut is the thing this project refuses to ship: these two
        # counts let the caller say "N개 중 M개" instead of just drawing M dots
        # and letting the other N-M vanish unremarked. `total_nodes` is the
        # draft's own node count, read the same way `node_candidates` reads it.
        "total_nodes": len(getattr(draft, "nodes", [])),
        "returned_nodes": len(candidates),
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


@router.patch("/phase-diagrams/{review_id}/timing")
def amend_phase_diagram_timing(
    review_id: str,
    payload: AmendPhaseDiagramTimingRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Correct ONE 신호 시간 value on the card — the uploader amending their record.

    THE USER'S DECISION (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도
    카드 단순화 — 도식+시간표, 짧은 확인 (#58)"): "황색시간 및 시간 같은거가 수정
    가능하게". The doctrine reconciliation recorded there is what this route
    implements — *editing a timing value on screen is the uploader amending
    their own statement, same authorship rule as #55's upload; server-stamps
    who/when* — so the editor is the signed-in user, the clock is this server's,
    and the previous value is kept in the review's amendment ledger rather than
    overwritten and forgotten.

    Nothing here confirms anything. The 확인 button still writes the one
    attestation, ``generate_node_signals`` still refuses an unbound mapping, and
    an amendment made against a review that carries no such plan or 현시 is a
    409 naming what IS on file — never a silently created row.
    """
    store = _phase_diagram_store()
    try:
        store.amend_timing(
            review_id,
            field=payload.field,
            value=payload.value,
            level_no=payload.level_no,
            plan_id=payload.plan_id,
            phase_id=payload.phase_id,
            edited_by=str(user.get("username") or user.get("id") or "").strip(),
        )
    except phase_diagram.PhaseDiagramRefused as exc:
        raise _phase_diagram_refusal(exc) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The amended 신호 시간 could not be written. Nothing was changed. "
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

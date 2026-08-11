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
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from routers.auth import current_user
from routers.datasets import MAX_UPLOAD_BYTES, _TOO_LARGE_DETAIL, _reject_oversized_body
from services import genesis_jobs, osm_fetch
from services.datasets import DatasetStorageError

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


@router.post("/jobs", status_code=202)
def start_job(
    payload: StartGenesisJobRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Start one generation. 202: the work happens on the job queue's thread.

    Returns the job record immediately; the caller polls ``GET /jobs/{id}``.
    Nothing is registered as a dataset here — ``gate_and_publish`` decides that,
    inside the job, after the gate has run.
    """
    try:
        osm_path = genesis_jobs.staged_osm_path(payload.upload_token)
    except genesis_jobs.StagedOsmNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        record = genesis_jobs.start_genesis_job(
            osm_path=osm_path,
            name=payload.name,
            exclude_service=payload.exclude_service,
            source_filename=osm_path.name,
            submitted_by=str(user.get("username") or ""),
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
    logger.info(
        "genesis: %s started generation %s (%s) in workspace %s",
        user.get("username"),
        record["id"],
        record["name"],
        record["workspace_id"],
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


__all__ = ["router"]

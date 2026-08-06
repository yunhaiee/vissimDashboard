"""Observation-set endpoints — persisted field counts (Phase 2 unit U10).

Workspace-scoped like every other data route: ``main.WorkspaceScopeMiddleware``
binds the request's workspace before the handler runs, and
``services.observations`` derives its per-workspace paths from it. A set
ingested in one workspace is invisible from another — physically, because the
registry file itself lives inside the workspace directory.

**Two-step upload, and why.** This backend has no ``python-multipart``
dependency (and ``requirements.txt`` is not this unit's to change), so a
``multipart/form-data`` handler would 500 on the first request. The flow is
therefore the one ``routers/validation.py``'s workbook-mapping UI already uses:
``POST /upload`` with the raw bytes as the body and the name in ``X-Filename``
(or ``?filename=``), once per workbook, then ``POST /ingest`` with a JSON body
naming the returned tokens plus the declared periods. Both the 413 guard and
its byte-level second check come from ``routers/datasets.py`` by import — the
same numbers, not a copy that can drift.

**A refusal is a 4xx with the reason as data.** ``ObservationRefusal.to_dict()``
becomes the response body: the stable ``reason_code``, the ko/en/ja
``message``, and (for a gate refusal) the gate's own findings verbatim —
code, severity, message, subject, citation and numeric context per issue. A
refused ingest stores nothing at all.

Read-only with respect to Vissim: nothing here opens COM, and nothing here
touches the active dataset.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

# Imported, not copied: one upload limit for the whole backend, so the two
# endpoints cannot drift to different numbers. Note that ``MAX_UPLOAD_BYTES``
# here is a BY-NAME copy -- ``_reject_oversized_body`` reads ``routers.datasets``'
# own module global, so anything that changes the limit (a test, a future
# config hook) has to change it there, and this name only backs the byte-level
# second check below.
from routers.datasets import MAX_UPLOAD_BYTES, _TOO_LARGE_DETAIL, _reject_oversized_body
from services import observations
from services.observations import (
    ObservationPeriod,
    ObservationRefusal,
    RegistryUnavailableError,
    StagedWorkbookNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/observations", tags=["observations"])


class ObservationIngestRequest(BaseModel):
    """What ``POST /ingest`` takes: staged uploads + the modeller's periods."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Human label for the set, e.g. '대전 유성구 4-5월'.")
    upload_tokens: list[str] = Field(
        min_length=1,
        max_length=observations.MAX_FILES_PER_SET,
        description="Tokens from POST /upload, one per workbook.",
    )
    periods: list[ObservationPeriod] = Field(
        min_length=1,
        max_length=observations.MAX_PERIODS_PER_SET,
        description=(
            "The named windows the counts are aggregated into. Required: which window "
            "represents the modelled period is the modeller's decision, never inferred from "
            "the data."
        ),
    )
    adapter: str | None = Field(
        default=None,
        description="Force a registered adapter by name; omitted = sniff and refuse if unsure.",
    )
    keep_uploads: bool = Field(
        default=False,
        description="Keep the staged files after a successful ingest (default: discard them).",
    )


@router.post("/upload")
async def upload_observation_workbook(
    request: Request, x_filename: str | None = Header(None)
):
    """Stage ONE count workbook and return its upload token.

    The file name is kept verbatim: the Daejeon adapter reads the site id and
    the declared collection window out of it (roadmap 1.2's documented
    deviation), so a staging area that renamed the upload would destroy both.
    """
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "")
    _reject_oversized_body(request)
    content = await request.body()
    # Second check on the bytes actually received: a chunked upload carries no
    # Content-Length, so the header check cannot be the only gate.
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
    try:
        return observations.stage_workbook(filename, content)
    except ObservationRefusal as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not write the uploaded workbook to the staging area. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc


@router.post("/ingest", status_code=201)
def ingest_observations(payload: ObservationIngestRequest):
    """Parse, gate, aggregate and store one observation set.

    422 with the gate's findings when a REFUSE-tier issue fires — nothing is
    stored in that case, deliberately: a set built from data the gate already
    refused would let a later reality check grade a run against numbers we know
    are wrong.
    """
    try:
        paths = [observations.staged_workbook_path(token) for token in payload.upload_tokens]
    except StagedWorkbookNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        record = observations.ingest_observation_set(
            paths,
            name=payload.name,
            periods=payload.periods,
            adapter_name=payload.adapter,
        )
    except ObservationRefusal as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except RegistryUnavailableError as exc:
        # The registry could not be read, so writing would destroy it. 409:
        # nothing changed, resolve it and retry — same reading (and the same
        # Windows causes) as routers/datasets.py's storage conflict.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not payload.keep_uploads:
        for token in payload.upload_tokens:
            observations.discard_staged_workbook(token)

    return {"stored": True, "set": record}


@router.get("")
def list_observation_sets() -> dict[str, Any]:
    """Every set in this workspace, newest first. Summaries only — the
    aggregates are on the detail route, so a listing stays bounded however many
    sites a drop carried."""
    return {
        "sets": observations.list_observation_sets(),
        "registry_health": observations.registry_health_status(),
    }


@router.get("/{set_id}")
def get_observation_set(set_id: str) -> dict[str, Any]:
    record = observations.get_observation_set(set_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No such observation set.")
    return record


@router.delete("/{set_id}")
def delete_observation_set(set_id: str) -> dict[str, Any]:
    try:
        deleted = observations.delete_observation_set(set_id)
    except RegistryUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="No such observation set.")
    return {"deleted": True, "id": set_id}

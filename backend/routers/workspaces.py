"""``/api/workspaces`` — list, create, and describe project workspaces.

A workspace (UI label: 프로젝트) is the isolation unit uploads live inside —
see ``services/workspaces.py`` for the model and the ``X-Workspace-Id``
request-scoping contract. This router is deliberately minimal for unit 1.4:
list, create, and "describe the one this request is scoped to". There is no
delete and no rename yet — both need a "what happens to this workspace's
simulation jobs and agent sessions" answer that belongs with the physical
per-workspace layout move (roadmap P③), not here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import workspaces
from services.datasets import get_active_dataset

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    #: Length is validated here AND in services.workspaces.create_workspace —
    #: the service is the authority (scripts and tests call it directly), this
    #: is just the HTTP boundary getting a clean 422 instead of a 500.
    name: str = Field(min_length=1, max_length=workspaces.MAX_WORKSPACE_NAME_CHARS)


@router.get("")
def list_workspaces():
    """Every workspace on this box, default first.

    ``current`` is the id this request resolved to (from ``X-Workspace-Id``,
    or the default when the header is absent), so a client can render the
    picker's selected state from one call.
    """
    entries = workspaces.list_workspaces()
    return {
        "workspaces": entries,
        "current": workspaces.current_workspace_id(),
        "default": workspaces.default_workspace_id(),
    }


@router.post("")
def create_workspace(payload: CreateWorkspaceRequest):
    try:
        return workspaces.create_workspace(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/current")
def current_workspace():
    """The request's workspace plus the dataset it resolves to.

    One call for the banner: which project am I in, and what model does it
    currently hold. The dataset block is exactly ``GET /api/datasets/active``'s
    payload — same resolution, no second source of truth.
    """
    workspace_id = workspaces.current_workspace_id()
    record = workspaces.get_workspace(workspace_id)
    if record is None:
        # Only reachable if the registry was edited/deleted between the
        # middleware's resolution and now; report it as a missing workspace
        # rather than inventing a record.
        raise HTTPException(
            status_code=404,
            detail={"code": "workspace_not_found", "message": f"Unknown workspace id: {workspace_id}"},
        )
    return {"workspace": record, "dataset": get_active_dataset()}

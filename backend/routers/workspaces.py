"""``/api/workspaces`` — list, create, and describe project workspaces.

A workspace (UI label: 프로젝트) is the isolation unit uploads live inside —
see ``services/workspaces.py`` for the model and the ``X-Workspace-Id``
request-scoping contract. This router is deliberately minimal for unit 1.4:
list, create, and "describe the one this request is scoped to". There is no
delete and no rename yet — both need a "what happens to this workspace's
simulation jobs and agent sessions" answer that belongs with the physical
per-workspace layout move (roadmap P③), not here.

**Unit P④ makes the listing personal.** ``GET /api/workspaces`` used to answer
"every workspace on this box" to anybody who asked, which for a consultancy is
a directory of client engagements handed out unauthenticated. It now returns
what the CALLER may see (``services.membership.visible_workspaces``): the
default workspace for an anonymous caller, plus their memberships for a
signed-in one, and everything for an admin. The listing and the middleware's
403 are two halves of one rule and must never disagree — a picker offering a
project the next request refuses is worse than either behaviour alone — so
both read ``services/membership.py`` and nothing else.

``POST`` now requires a signed-in user, and grants the creator membership in
the same call. Not gatekeeping for its own sake: without the grant, creating a
project would produce one the creator is immediately 403'd out of, and without
the sign-in there is nobody to grant it TO. "Create a project, then use it" has
to keep working in one step.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from routers.auth import current_user, optional_current_user
from services import membership, workspaces
from services.datasets import get_active_dataset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    #: Length is validated here AND in services.workspaces.create_workspace —
    #: the service is the authority (scripts and tests call it directly), this
    #: is just the HTTP boundary getting a clean 422 instead of a 500.
    name: str = Field(min_length=1, max_length=workspaces.MAX_WORKSPACE_NAME_CHARS)


@router.get("")
def list_workspaces(user: dict[str, Any] | None = Depends(optional_current_user)):
    """The workspaces THIS caller may see, default first.

    ``current`` is the id this request resolved to (from ``X-Workspace-Id``,
    or the default when the header is absent), so a client can render the
    picker's selected state from one call. It is always in the returned list:
    the membership middleware already refused any request naming a workspace
    this caller cannot enter, so reaching this handler proves access.
    """
    entries = membership.visible_workspaces(user)
    return {
        "workspaces": entries,
        "current": workspaces.current_workspace_id(),
        "default": workspaces.default_workspace_id(),
    }


@router.post("")
def create_workspace(
    payload: CreateWorkspaceRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Create a project and make the creator its first member.

    The grant is not a convenience — it is what keeps the endpoint honest. A
    new workspace has no members, and ``services.membership.user_can_access``
    admits nobody but an admin to a non-default workspace, so an ungranted
    creation would hand back an id whose very next request is a 403.

    A failed grant does NOT roll the workspace back (the registry write already
    landed, and there is no delete path to undo it with) — it is logged and
    re-raised as a 500 so the operator sees a half-finished creation instead of
    a project that silently belongs to nobody. An admin can always repair it
    from the admin UI, which is the reason admins are allowed everywhere.
    """
    try:
        record = workspaces.create_workspace(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    membership.grant(record["id"], int(user["id"]))
    logger.info(
        "workspaces: %s created project %s (%s) and was granted membership",
        user["username"],
        record["id"],
        record["name"],
    )
    return record


@router.get("/current")
def current_workspace():
    """The request's workspace plus the dataset it resolves to.

    One call for the banner: which project am I in, and what model does it
    currently hold. The dataset block is exactly ``GET /api/datasets/active``'s
    payload — same resolution, no second source of truth.

    No auth dependency: the membership middleware has already decided whether
    this caller may be scoped to this workspace at all, so a second check here
    would be a second, drifting copy of the rule.
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

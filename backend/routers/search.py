"""``GET /api/network/search`` — find a network component by name or number.

Thin transport over :mod:`services.network_search`: resolve the active
dataset, delegate, attach the same ``source`` marker
``routers/network.py`` already stamps on its payload so a client can tell
which network the hits belong to.

**Bounded by construction.** ``limit`` is clamped into ``[1, MAX_LIMIT]`` by
the service (never a 422 for an over-eager search box) and the response
always carries ``total`` next to ``returned``/``truncated`` — the
2026-07-27 observation-flood lesson: a consumer must never be handed an
unbounded list, and must never be able to mistake a truncated list for a
complete one.

Deliberately NOT supported here (unlike ``routers/network.py``'s ``GET
/api/network``): a ``session_id`` override that searches an agent session's
working copy. It would need that module's private ``_resolve_working_inpx``
path-safety helper, and there is no consumer yet — the search box lives on
the network page's active view. Adding it later is a query param plus one
shared helper, not a reshape of this response.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Query

from services import network_search
from services.datasets import get_active_inpx_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/network", tags=["network"])


@router.get("/search")
def search_network(
    q: str = Query("", description="Component id, name fragment, or class name (ko/en/ja)."),
    limit: int = Query(
        network_search.DEFAULT_LIMIT,
        description=f"Max results to return; clamped into [1, {network_search.MAX_LIMIT}].",
    ),
):
    inpx_path = get_active_inpx_path()
    if not os.path.isfile(inpx_path):
        raise HTTPException(status_code=404, detail=f"INPX file not found at {inpx_path}")

    payload = network_search.search(q, limit=limit, inpx_path=inpx_path)
    payload["source"] = {"view": "active"}
    return payload


@router.get("/search/families")
def search_families():
    """The searchable-family catalogue (type token, backing model list, and
    the query aliases that browse the whole class). Debug/introspection —
    ``aliases`` are matching vocabulary, never display labels."""
    return {"families": network_search.searchable_families()}

import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from services.agent.sessions import SESSIONS_ROOT
from services.datasets import get_active_inpx_path
from services.inpx_parser import parse_network, parse_network_root
from services.scenario_project import build_scenario_tree, can_build_scenario_tree

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/network", tags=["network"])

_cache: dict[tuple[float, str, str], dict] = {}

# P2 (edited-network view + refresh): GET /api/network?session_id=<id> optionally
# overlays a live agent-editing session's *working copy*
# (services.agent.state.ArtifactManager.working_path, i.e.
# runtime/agent_sessions/<session_id>/working.inpx) in place of the active
# uploaded/default dataset -- see docs/phase2/DECISIONS.md's P2 entry for why
# session_id must be passed explicitly by the client rather than resolved as
# "most recently modified working copy on disk": runtime/agent_sessions/
# accumulates one directory per agent turn across every eval run this repo has
# ever done, and services.agent.sessions's TTL sweep only clears entries older
# than its (7-day-default) TTL, so "most recent" would routinely pick up a
# stale directory from an unrelated session/run rather than the user's own
# open chat.
#
# Deliberately hex-only and length-capped: a plain, unambiguous directory-name
# shape that cannot itself contain a path separator or ".." segment, so it can
# never escape SESSIONS_ROOT regardless of how the OS/pathlib normalizes it --
# tighter than routers/assistant.py's own client-input-boundary `_SESSION_ID_RE`
# (`^[0-9a-f]{8,64}$`) since this endpoint has no legitimate reason to accept
# anything looser than a real `uuid.uuid4().hex[:12]` session id.
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


def _resolve_working_inpx(session_id: str | None) -> Path | None:
    """Best-effort resolution of ``session_id``'s working-copy .inpx.

    Returns ``None`` for anything that isn't a clean, existing working copy:
    no session_id, a session_id failing :data:`_SESSION_ID_RE`, a session
    directory that doesn't exist, one whose resolved path would fall outside
    :data:`SESSIONS_ROOT` (defense in depth -- the regex already rules this
    out, but never trust a single check alone against path construction), or
    one with no ``working.inpx`` yet (e.g. a session that has only asked
    read-only questions so far, or a stale eval-run directory).

    Never raises: the caller's contract is "fall back to the active dataset
    with source.view == 'active'", never a 500, for any bad/missing session.
    """
    if not session_id or not _SESSION_ID_RE.match(session_id):
        return None

    sessions_root = SESSIONS_ROOT.resolve()
    session_dir = (sessions_root / session_id).resolve()
    try:
        session_dir.relative_to(sessions_root)
    except ValueError:
        logger.warning("network: session_id %r resolved outside SESSIONS_ROOT; ignoring", session_id)
        return None

    working_path = session_dir / "working.inpx"
    return working_path if working_path.is_file() else None


@router.get("")
def get_network(scenario_id: str | None = Query(None), session_id: str | None = Query(None)):
    working_path = _resolve_working_inpx(session_id)
    if working_path is not None and session_id is not None:
        inpx_path: Path = working_path
        source: dict[str, str] = {"view": "working", "session_id": session_id}
    else:
        inpx_path = get_active_inpx_path()
        source = {"view": "active"}

    try:
        mtime = os.path.getmtime(inpx_path)
    except OSError:
        if source["view"] == "working":
            # Vanished between resolution and stat (a concurrent agent turn
            # rewriting/rolling back the working copy) -- degrade to the
            # active dataset instead of 404ing a viewer that only asked to
            # see *something*.
            inpx_path = get_active_inpx_path()
            source = {"view": "active"}
            try:
                mtime = os.path.getmtime(inpx_path)
            except OSError:
                raise HTTPException(status_code=404, detail=f"INPX file not found at {inpx_path}")
        else:
            raise HTTPException(status_code=404, detail=f"INPX file not found at {inpx_path}")

    # Cache key includes the resolved path (not just scenario_id), so the
    # working copy and the active dataset -- and different sessions' working
    # copies -- never collide in `_cache`.
    cache_key = (mtime, str(inpx_path), scenario_id or "")
    if cache_key in _cache:
        return _cache[cache_key]

    if can_build_scenario_tree(scenario_id):
        logger.info("Parsing network for scenario %s from %s", scenario_id, inpx_path)
        tree = build_scenario_tree(inpx_path, scenario_id)
        result = parse_network_root(tree.getroot())
    else:
        logger.info("Parsing base network from %s", inpx_path)
        result = parse_network(str(inpx_path))

    result["source"] = source
    _cache[cache_key] = result
    return result

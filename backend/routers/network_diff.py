"""Roadmap row 2.4 — the network diff view's read-only backend.

``GET /api/network/diff?session_id=<id>&base=<token>&target=<token>`` compares
two snapshots of ONE agent session's network and returns
``services.genesis.diff``'s :class:`StructuralDiff` as JSON. MRWA's "Model
Audit View" framing: what changed between two states of the model, stated as
structure, with the position question answered by a stated method rather than
by a number nobody can defend.

Why a token space and not two version numbers
---------------------------------------------
The obvious surface — "pick two ``edit_NNN.inpx`` versions" — was measured
against what this box actually holds before it was designed, and it is empty.
``runtime/agent_sessions/`` here has **1572 session directories; 0 of them have
two or more ``edit_NNN.inpx``**. 494 have exactly one, 1078 have none, and all
1572 have a ``working.inpx``. A version-pair picker would therefore render an
empty dropdown for every session on the machine — a surface with no reachable
input is not a surface.

So ``base`` and ``target`` each take a token from ONE space, and the space is
built from the four snapshots that actually exist on disk:

``0``
    The session's **pristine source** — the file a rollback-to-version-0
    restores from. Resolved exactly the way
    ``services.agent.tools_edit._session_pristine_source`` resolves it: the
    ``source_inpx`` recorded in ``session_meta.json`` when the session was
    created, falling back to the workspace's live active dataset for a
    pre-1.4 session that has no sidecar. Available for all 1572.
``1`` … ``999``
    ``edit_NNN.inpx`` — one immutable snapshot per successfully dispatched and
    postwrite-verified write (``services/agent/state.py``'s
    ``ArtifactManager.save_version``). Available for 494.
``working``
    ``working.inpx``, the mutable current copy. Available for all 1572.
``active``
    The requesting workspace's currently active dataset. NOT the same file as
    ``0`` once the dataset has been swapped mid-session, which is the whole
    reason both tokens exist.

``base=0&target=working`` is therefore meaningful for every session that has
ever cloned, and ``base=0&target=1`` / ``base=1&target=working`` for every
session that has ever written. The brief's two proposed shapes ("two artifact
versions" and "``base=active`` vs ``target=working``") are both points inside
this one space, so no mode flag is needed and no combination is unreachable.

One deliberate imprecision, stated rather than hidden: ``ArtifactManager.
rollback_to(0)`` re-applies ``_disable_scenario_management_flag`` and
``_force_single_run`` to the file it restores, so version 0 as *restored* is
not byte-identical to ``source_inpx`` as *read here*. Those two normalizations
rewrite exactly one attribute each — ``underScenMngm`` on ``<netPara>`` and
``numRuns`` on ``<simulation>`` — and ``genesis.diff``'s topology extraction
reads neither, so the two files are identical as far as THIS comparison is
concerned. Recorded because a future consumer that starts reading simulation
parameters off the same token would inherit the gap.

Read-only, and no COM
---------------------
Every function here only ever ``stat()``s and reads bytes under a session work
dir or the active dataset. Nothing writes, deletes, or touches
``ArtifactManager``; nothing launches Vissim. Viewing a diff can never mutate
the session it describes — the same promise ``routers/agent_downloads.py``
makes, for the same reason.

Ownership
---------
Session-scoped, so it asks the same question every other session-scoped
endpoint asks: :func:`services.agent.sessions.session_workspace_matches`, via
``services.audit_trail.resolve_session_dir`` (the shared helper that already
bundles the hex-id allowlist, the ``relative_to`` re-check and the ownership
call). A session owned by another workspace 404s **byte-identically** to a
session id that has never existed — one ``detail`` string, one code path, no
existence oracle. A version that does not exist on an OWNED session gets its
own distinct 404, which leaks nothing: an intruder never reaches it.

Caching
-------
The parse+diff of two .inpx files is the expensive part and both inputs are
immutable-or-mtime-stamped, so the result is memoised on
``(base_path, base_mtime, target_path, target_mtime)`` — the same shape
``routers/network.py``'s ``_cache`` uses, for the same reason: a working copy,
an active dataset and two different sessions' artifacts must never collide,
and a file that changed underneath must never serve a stale answer.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.agent.sessions import read_session_meta_dir
from services.audit_trail import resolve_session_dir
from services.datasets import get_active_inpx_path
from services.genesis.diff import StructuralDiff, diff_network_files

logger = logging.getLogger(__name__)

# Fully-specified sub-path of the /api/network space `routers/network.py`
# already claims. Distinct, complete routes -- exactly the arrangement
# `routers/search.py` uses under the same prefix -- so registration order in
# main.py cannot affect matching.
router = APIRouter(prefix="/api/network/diff", tags=["network-diff"])

#: Matches ``ArtifactManager``'s ``edit_NNN.inpx`` snapshot naming. Mirrors
#: ``routers/agent_downloads.py``'s own ``_EDIT_FILE_RE`` -- duplicated rather
#: than imported for the same reason that module gives: the authoritative
#: ``_VERSION_RE`` in ``services/agent/state.py`` is module-private by design.
_EDIT_FILE_RE = re.compile(r"^edit_(\d{3})\.inpx$")

#: A numeric version token. ``0`` is handled separately (it is the pristine
#: source, not an ``edit_000.inpx`` -- that file never exists), so this covers
#: the saved snapshots only.
_NUMERIC_VERSION_RE = re.compile(r"^[1-9]\d{0,2}$")

#: The two non-numeric tokens.
TOKEN_WORKING = "working"
TOKEN_ACTIVE = "active"
TOKEN_PRISTINE = "0"

#: One string for "this session is not yours" AND "this session does not
#: exist". Same wording ``routers/agent_downloads.py`` uses, so the two
#: endpoints cannot be played off each other either.
_NO_SESSION_DETAIL = "No matching session."


# ---------------------------------------------------------------------------
# Snapshot resolution
# ---------------------------------------------------------------------------


def _session_versions(session_dir: Path) -> list[int]:
    """Every retained ``edit_NNN.inpx`` version number, ascending. Empty for a
    session that has cloned but never saved a write."""
    versions: list[int] = []
    try:
        entries = list(session_dir.iterdir())
    except OSError:
        return versions
    for path in entries:
        match = _EDIT_FILE_RE.match(path.name)
        if match and path.is_file():
            versions.append(int(match.group(1)))
    return sorted(versions)


def _pristine_source(session_dir: Path) -> tuple[Path, str]:
    """The .inpx a rollback-to-0 would restore this session's working copy from,
    plus HOW it was resolved.

    Mirrors ``services.agent.tools_edit._session_pristine_source`` (private
    there, and importing that module would pull the whole write-tool registry
    into a read-only viewer). The ``resolved_from`` half is this endpoint's
    own addition: for a pre-1.4 session with no sidecar, token ``0`` and token
    ``active`` collapse onto the same file, and the UI is entitled to say so
    rather than present two pickers that silently mean one thing.
    """
    recorded = read_session_meta_dir(session_dir).get("source_inpx")
    if recorded:
        return Path(str(recorded)), "session_meta"
    return get_active_inpx_path(), "active_dataset_fallback"


def _resolve_token(token: str, session_dir: Path) -> dict[str, Any]:
    """One token -> the file it names, or an HTTP error.

    Raises ``HTTPException(404)`` for a well-formed token naming a snapshot
    this session does not have, and ``HTTPException(422)`` for a token that is
    not in the space at all -- a distinction the UI needs, since the first is
    "pick a different version" and the second is "this client is broken".
    """
    if token == TOKEN_PRISTINE:
        path, resolved_from = _pristine_source(session_dir)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="This session's original network is no longer on disk.",
            )
        return {
            "token": token,
            "kind": "pristine",
            "version": 0,
            "path": path,
            "resolved_from": resolved_from,
        }

    if token == TOKEN_WORKING:
        path = session_dir / "working.inpx"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="This session has no working copy yet.")
        return {"token": token, "kind": "working", "version": None, "path": path, "resolved_from": "session"}

    if token == TOKEN_ACTIVE:
        path = get_active_inpx_path()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No active dataset to compare against.")
        return {"token": token, "kind": "active", "version": None, "path": path, "resolved_from": "workspace"}

    if _NUMERIC_VERSION_RE.match(token):
        version = int(token)
        path = session_dir / f"edit_{version:03d}.inpx"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No matching version for this session.")
        return {
            "token": token,
            "kind": "version",
            "version": version,
            "path": path,
            "resolved_from": "session",
        }

    raise HTTPException(
        status_code=422,
        detail="base/target must be 0, a saved version number, 'working' or 'active'.",
    )


def _side_payload(resolved: dict[str, Any], mtime: float) -> dict[str, Any]:
    """The client-facing description of one side. ``path`` is deliberately NOT
    included: an absolute path off this machine is server detail a browser has
    no use for, and the pristine source's path in particular names a directory
    outside the session."""
    path: Path = resolved["path"]
    return {
        "token": resolved["token"],
        "kind": resolved["kind"],
        "version": resolved["version"],
        "filename": path.name,
        "resolved_from": resolved["resolved_from"],
        # ISO 8601 UTC -- the same shape `routers/agent_downloads.py` reports
        # `modified_at` in, so one frontend formatter serves both surfaces.
        "modified_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Serialisation
#
# StructuralDiff is a pydantic model, so `model_dump` IS the serializer and
# nothing here re-derives a field. What it does not carry is the model's
# @property surface (change_count, has_changes, the per-row `changed` flags,
# the coverage ratios, lane deltas, movement labels) -- those are computed
# attributes, not fields. They are READ OFF the model below rather than
# recomputed, so the numbers the view renders are the same numbers
# `render_diff_report` renders.
# ---------------------------------------------------------------------------


def _serialize_diff(diff: StructuralDiff) -> dict[str, Any]:
    payload = diff.model_dump(mode="json")
    payload["ok"] = diff.ok
    payload["change_count"] = diff.change_count
    payload["has_changes"] = diff.has_changes

    if diff.links is not None and payload.get("links") is not None:
        payload["links"]["change_count"] = diff.links.change_count
        for row, model in zip(payload["links"]["lane_count_changed"], diff.links.lane_count_changed):
            row["delta"] = model.delta

    if diff.connectors is not None and payload.get("connectors") is not None:
        payload["connectors"]["change_count"] = diff.connectors.change_count
        for row, model in zip(
            payload["connectors"]["lane_count_changed"], diff.connectors.lane_count_changed
        ):
            row["delta"] = model.delta

    for row, model in zip(payload.get("node_movements") or [], diff.node_movements or []):
        row["changed"] = model.changed
        for ref_row, ref in zip(row["added"], model.added):
            ref_row["label"] = ref.label
        for ref_row, ref in zip(row["removed"], model.removed):
            ref_row["label"] = ref.label

    for row, model in zip(payload.get("signal_coverage") or [], diff.signal_coverage or []):
        row["changed"] = model.changed
        row["base_coverage"] = model.base_coverage
        row["target_coverage"] = model.target_coverage

    return payload


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

#: ``(base_path, base_mtime, target_path, target_mtime)`` -> serialized diff.
#: Same shape and same rationale as ``routers/network.py``'s ``_cache``.
_cache: dict[tuple[str, float, str, float], dict[str, Any]] = {}


def _cached_diff(
    base_path: Path,
    base_mtime: float,
    base_label: str,
    target_path: Path,
    target_mtime: float,
    target_label: str,
) -> dict[str, Any]:
    key = (str(base_path), base_mtime, str(target_path), target_mtime)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    logger.info("network_diff: comparing %s -> %s", base_path, target_path)
    diff = diff_network_files(
        base_path, target_path, base_label=base_label, target_label=target_label
    )
    payload = _serialize_diff(diff)
    _cache[key] = payload
    return payload


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("")
def network_diff(
    session_id: str = Query(..., description="Agent session whose snapshots are compared"),
    base: str = Query(TOKEN_PRISTINE, description="0 | <version> | working | active"),
    target: str = Query(TOKEN_WORKING, description="0 | <version> | working | active"),
):
    """Structural diff of two snapshots of one agent session's network.

    Defaults to ``base=0&target=working`` — "everything this session has done
    to the model so far", the question the view exists to answer, and the one
    pair that is available for every session that has ever cloned.

    ``available`` is returned on every successful response so a picker can be
    built from the same call that renders the diff, rather than from a second
    round trip that could disagree with it.

    ``identical_sources`` is true when both tokens resolved to the SAME file —
    ``base=working&target=working``, or ``base=0&target=active`` on a
    sidecar-less session where the two collapse. The diff is then genuinely
    empty, and saying WHY it is empty is not the same as saying nothing
    changed.
    """
    session_dir = resolve_session_dir(session_id)
    if session_dir is None:
        # Foreign session and non-existent session take this one branch, so
        # their responses are byte-identical.
        raise HTTPException(status_code=404, detail=_NO_SESSION_DETAIL)

    resolved_base = _resolve_token(base, session_dir)
    resolved_target = _resolve_token(target, session_dir)

    base_path: Path = resolved_base["path"]
    target_path: Path = resolved_target["path"]

    try:
        base_mtime = os.path.getmtime(base_path)
        target_mtime = os.path.getmtime(target_path)
    except OSError:
        # Vanished between resolution and stat (a concurrent agent turn
        # rewriting the working copy, a dataset swap). Not a 500: the caller
        # asked for a snapshot that no longer exists.
        raise HTTPException(status_code=404, detail="A network being compared is no longer on disk.")

    base_label = _snapshot_label(resolved_base)
    target_label = _snapshot_label(resolved_target)

    payload = _cached_diff(
        base_path, base_mtime, base_label, target_path, target_mtime, target_label
    )

    versions = _session_versions(session_dir)
    pristine_path, _ = _pristine_source(session_dir)
    return {
        "session_id": session_id,
        "session_id_short": session_id[:8],
        "base": _side_payload(resolved_base, base_mtime),
        "target": _side_payload(resolved_target, target_mtime),
        "identical_sources": base_path.resolve() == target_path.resolve(),
        "available": {
            "versions": versions,
            "has_working": (session_dir / "working.inpx").is_file(),
            "has_pristine": pristine_path.is_file(),
        },
        "diff": payload,
    }


def _snapshot_label(resolved: dict[str, Any]) -> str:
    """The name a snapshot is called by inside the diff's own findings and
    summaries. Stable and machine-ish on purpose — ``NetworkSummary.label`` is
    embedded in ``DiffFinding.message`` prose the UI shows verbatim, so it must
    not change with the viewer's language."""
    kind = resolved["kind"]
    if kind == "pristine":
        return "original (v0)"
    if kind == "version":
        return f"edit {resolved['version']}"
    if kind == "working":
        return "working copy"
    return "active dataset"

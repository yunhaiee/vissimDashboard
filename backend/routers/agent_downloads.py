"""P8: retrieve the .inpx an agent session has actually edited.

Every agent write (``services/agent/state.py``'s ``ArtifactManager``) lands
in ``runtime/agent_sessions/<session_id>/`` as ``working.inpx`` (the mutable
current copy) plus an immutable, monotonically-numbered ``edit_NNN.inpx`` per
successfully dispatched + postwrite-verified write tool -- see
``ArtifactManager.save_version``'s docstring. Until this router existed, that
working copy was trapped in internal storage: there was no way for a user to
get the edited model back out to open in Vissim themselves.

Read-only with respect to agent state by construction: every function here
only ever ``Path.iterdir()``/``Path.read_bytes()``s inside a session's work
dir, never writes, deletes, or touches ``ArtifactManager`` (no ``clone()``,
``save_version()``, or ``rollback_to()`` call anywhere in this module) --
listing or downloading a session's models can never mutate it or its
history, and never launches Vissim COM.

**Scope note (session <-> dataset association):** ``AgentState.artifacts``
is always ``{}`` in production (see ``services/agent/state.py``'s own
docstring on ``AgentState``) -- no session records which dataset it was
cloned from. This app has exactly one active dataset slot at a time
(``services.datasets.get_active_inpx_path``), so for the ordinary case
"which dataset was this session's model edited from" is simply "whatever is
active now"; a session outliving a *later* dataset swap would report the
new dataset's stem in its download filename, not the one it was actually
cloned from. Documented as a known limitation (drafted docs/phase2/
DECISIONS.md entry in the P8 report) rather than solved here -- solving it
properly needs a new persisted field on session state, out of this task's
"expose what exists, don't build a new versioning store" scope.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from services.agent.sessions import SESSIONS_ROOT
from services.agent.state import SIBLING_ASSET_EXTENSIONS
from services.datasets import get_active_inpx_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent/downloads", tags=["agent-downloads"])

# Deliberately hex-only and length-capped -- mirrors routers/network.py's
# `_SESSION_ID_RE` (tighter than routers/assistant.py's own
# `^[0-9a-f]{8,64}$` client-input boundary): a plain, unambiguous directory-
# name shape that cannot itself contain a path separator or ".." segment,
# and the only shape a real `uuid.uuid4().hex[:12]` session id ever has.
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

#: Matches ArtifactManager's `edit_NNN.inpx` snapshot naming exactly (mirrors
#: services/agent/state.py's own private `_VERSION_RE` -- duplicated rather
#: than imported since that name is module-private there by design; this is
#: the same small, deliberate duplication session_store.py's own docstring
#: already accepts for `SESSIONS_ROOT`-adjacent constants).
_EDIT_FILE_RE = re.compile(r"^edit_(\d{3})\.inpx$")

#: The exact stem `services.agent.edit_session.VissimEditSession.persist()`
#: writes its SaveNetAs temp file under (`working_inpx.stem + "_saving"`,
#: and `working_inpx` is always literally `working.inpx` -- ArtifactManager.
#: __init__ hardcodes it). LIVE FINDING while building this feature: Vissim's
#: SaveNetAs of `working_saving.inpx` also emits a same-stem
#: `working_saving.layx` alongside it (observed in a real, non-synthetic
#: session dir on this dev box), but `persist()` only `os.replace()`s the
#: .inpx half onto `working.inpx` -- the sibling is left behind forever
#: under its "_saving" name, never becoming `working.layx`. A NEW, previously
#: undocumented gap (drafted as a docs/phase2/DECISIONS.md follow-up in the
#: P8 report, out of THIS task's scope to fix -- persist() lives in
#: edit_session.py and touching it needs live-Vissim re-verification). This
#: constant exists purely so the download endpoint doesn't ship that
#: confusingly-named leftover as if it were a real sibling asset.
_SAVING_LEFTOVER_STEM = "working_saving"

_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 200

#: Characters illegal (or awkward) in a Windows/macOS/Linux filename --
#: applied to the active dataset's stem before it goes into a download's
#: Content-Disposition filename, since that stem can come from a
#: user-uploaded filename (services/datasets.py's `_write_metadata` only
#: strips directory components via `Path(filename).name`, not illegal
#: characters).
_UNSAFE_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _resolve_session_dir(session_id: str) -> Path | None:
    """Mirrors routers/network.py's `_resolve_working_inpx` defense-in-depth:
    regex-validate, then re-check the resolved path actually lands inside
    SESSIONS_ROOT before trusting it. Returns ``None`` (never raises) for
    anything that isn't a clean, existing session directory."""
    if not session_id or not _SESSION_ID_RE.match(session_id):
        return None

    sessions_root = SESSIONS_ROOT.resolve()
    session_dir = (sessions_root / session_id).resolve()
    try:
        session_dir.relative_to(sessions_root)
    except ValueError:
        logger.warning("agent_downloads: session_id %r resolved outside SESSIONS_ROOT; ignoring", session_id)
        return None

    return session_dir if session_dir.is_dir() else None


def _session_versions(session_dir: Path) -> list[int]:
    """Every retained ``edit_NNN.inpx`` version number in ``session_dir``,
    ascending. Empty for a session that has cloned but never saved a write."""
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


def _qualifying_siblings(session_dir: Path) -> list[Path]:
    """Files in ``session_dir`` a download zip should bundle alongside the
    .inpx: allowlisted extension (the SAME allowlist ``ArtifactManager.
    clone()`` copies FROM, so a zip's contents are exactly what clone()
    would have put there), excluding the `working_saving.*` persist()
    leftover (see `_SAVING_LEFTOVER_STEM`)."""
    siblings: list[Path] = []
    try:
        entries = list(session_dir.iterdir())
    except OSError:
        return siblings
    for path in entries:
        if not path.is_file():
            continue
        if path.suffix.lower() not in SIBLING_ASSET_EXTENSIONS:
            continue
        if path.stem == _SAVING_LEFTOVER_STEM:
            continue
        siblings.append(path)
    return siblings


def _short_session_id(session_id: str) -> str:
    return session_id[:8]


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _safe_dataset_stem() -> str:
    stem = get_active_inpx_path().stem or "network"
    cleaned = _UNSAFE_FILENAME_CHARS_RE.sub("_", stem).strip().strip(".")
    return cleaned or "network"


def _entry_for_version(
    session_id: str, session_dir: Path, version: int, *, is_latest: bool, dataset_filename: str
) -> dict[str, Any]:
    path = session_dir / f"edit_{version:03d}.inpx"
    return {
        "session_id": session_id,
        "session_id_short": _short_session_id(session_id),
        "version": version,
        "is_latest": is_latest,
        "filename": path.name,
        "modified_at": _iso_mtime(path),
        "has_siblings": bool(_qualifying_siblings(session_dir)),
        "dataset_filename": dataset_filename,
    }


def _entry_for_unsaved_working_copy(
    session_id: str, session_dir: Path, *, dataset_filename: str
) -> dict[str, Any] | None:
    """Fallback entry for a session that has ``clone()``-d but never
    ``save_version()``-d -- "if only the current working copy exists per
    session, list that" (this task's brief). Only used for a direct
    ``?session_id=`` lookup (see ``list_downloads``'s docstring for why the
    unscoped listing excludes these)."""
    working_path = session_dir / "working.inpx"
    if not working_path.is_file():
        return None
    return {
        "session_id": session_id,
        "session_id_short": _short_session_id(session_id),
        "version": None,
        "is_latest": True,
        "filename": working_path.name,
        "modified_at": _iso_mtime(working_path),
        "has_siblings": bool(_qualifying_siblings(session_dir)),
        "dataset_filename": dataset_filename,
    }


@router.get("")
def list_downloads(
    session_id: str | None = Query(None, description="Scope to one session; omit to list recent edited sessions"),
    limit: int = Query(_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
):
    """List downloadable edited models.

    ``session_id`` given: every RETAINED version of that one session,
    newest-first (versions are never rewritten or reused -- see
    ``ArtifactManager``'s own docstring -- so this is a complete history).
    A session that exists but has never saved a write still gets one entry
    for its unsaved working copy (``version: null``) -- see
    ``_entry_for_unsaved_working_copy``. An unknown/invalid/missing session
    degrades to an empty list rather than a 404 -- this is a listing
    endpoint, "nothing to show" is a normal, not an error, response.

    ``session_id`` omitted: the LATEST version only, one row per session
    that has actually saved at least one write, across every session under
    ``runtime/agent_sessions/`` -- sorted by modified time, newest first,
    capped at ``limit``. Sessions that only ever asked read-only questions
    (no ``edit_NNN.inpx`` ever written) are excluded here -- there is
    nothing edited to download, and this repo's session directory
    accumulates one entry per agent turn across every eval run ever done
    (routers/network.py's own P2 comment: "~60 stale" on that dev machine,
    over a thousand on this one), so silently including them would flood
    the list with noise no user asked to browse. This intentionally does
    NOT try to filter to "sessions cloned from today's active dataset" --
    that association isn't persisted anywhere (see this module's docstring)
    -- so a dataset swap can surface an older session's model here; each
    row still names its own session id so that's never ambiguous to the
    user, just not automatically hidden.

    Each entry's ``dataset_filename`` is the CURRENT active dataset's own
    filename (``services.datasets.get_active_inpx_path().name``) -- the same
    best-effort proxy ``download_model`` uses for its filename convention,
    with the same documented caveat: a session that outlives a later dataset
    swap reports the NEW dataset's name here, not the one it actually cloned
    from (see this module's docstring).
    """
    dataset_filename = get_active_inpx_path().name

    if session_id is not None:
        session_dir = _resolve_session_dir(session_id)
        if session_dir is None:
            return []
        versions = _session_versions(session_dir)
        if not versions:
            fallback = _entry_for_unsaved_working_copy(session_id, session_dir, dataset_filename=dataset_filename)
            return [fallback] if fallback is not None else []
        latest = versions[-1]
        return [
            _entry_for_version(
                session_id, session_dir, version, is_latest=(version == latest), dataset_filename=dataset_filename
            )
            for version in reversed(versions)
        ]

    if not SESSIONS_ROOT.is_dir():
        return []

    try:
        candidates = [entry for entry in SESSIONS_ROOT.iterdir() if entry.is_dir()]
    except OSError:
        logger.warning("agent_downloads: cannot list %s", SESSIONS_ROOT)
        return []

    entries: list[dict[str, Any]] = []
    for session_dir in candidates:
        versions = _session_versions(session_dir)
        if not versions:
            continue
        latest = versions[-1]
        try:
            entries.append(
                _entry_for_version(
                    session_dir.name, session_dir, latest, is_latest=True, dataset_filename=dataset_filename
                )
            )
        except OSError:
            # edit_NNN.inpx vanished between the glob and the stat (a
            # concurrent sweep/rollback) -- skip this session, not fatal.
            continue

    entries.sort(key=lambda entry: entry["modified_at"], reverse=True)
    return entries[:limit]


@router.get("/{session_id}/{version}")
def download_model(session_id: str, version: str):
    """Stream one session's edited model: a zip (.inpx + sibling assets) when
    any qualifying sibling exists in the work dir, else the bare .inpx.

    ``version`` is either ``latest`` (the highest retained ``edit_NNN.inpx``,
    or the unsaved ``working.inpx`` if the session has never saved a write)
    or a literal version number matching an existing ``edit_NNN.inpx``. Both
    are validated against a fixed pattern and, for a numeric version, only
    ever used to build a filename of the exact shape ``edit_NNN.inpx`` that
    is then checked to actually exist -- never a client-supplied path.

    Read-only: only ever reads bytes off disk under ``session_id``'s work
    dir. Never touches ``ArtifactManager`` -- a download can never mutate
    the session it reads from.
    """
    session_dir = _resolve_session_dir(session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="No matching session.")

    version_label: str
    target_path: Path
    if version == "latest":
        versions = _session_versions(session_dir)
        if versions:
            target_path = session_dir / f"edit_{versions[-1]:03d}.inpx"
            version_label = f"{versions[-1]:03d}"
        else:
            working_path = session_dir / "working.inpx"
            if not working_path.is_file():
                raise HTTPException(status_code=404, detail="This session has no edited model yet.")
            target_path = working_path
            version_label = "working"
    elif re.match(r"^[1-9]\d{0,2}$", version):
        version_int = int(version)
        target_path = session_dir / f"edit_{version_int:03d}.inpx"
        if not target_path.is_file():
            raise HTTPException(status_code=404, detail="No matching version for this session.")
        version_label = f"{version_int:03d}"
    else:
        raise HTTPException(status_code=404, detail="No matching version for this session.")

    dataset_stem = _safe_dataset_stem()
    short_id = _short_session_id(session_id)
    siblings = _qualifying_siblings(session_dir)

    if siblings:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            # Renamed to the dataset's own stem (never "working"/"edit_003")
            # so it drops into a folder that looks like a normal Vissim
            # project -- and, since every sibling below keeps ITS exact
            # original name (never renamed), a same-stem .layx among them
            # automatically keeps matching this renamed .inpx: Vissim's
            # "auto-apply the same-directory-same-stem layout on open"
            # convention is purely filename-based (confirmed live: 0
            # references to ".layx" anywhere inside a real .inpx's own XML),
            # so renaming ONLY the .inpx reconstructs it for free. Any OTHER
            # sibling (.vap/.pua/.prbc/images) is referenced by the .inpx's
            # own (byte-unmodified) XML via its EXACT original filename, so
            # renaming those would break the very thing this endpoint exists
            # to preserve.
            archive.write(target_path, arcname=f"{dataset_stem}.inpx")
            for sibling in siblings:
                archive.write(sibling, arcname=sibling.name)
        content = buffer.getvalue()
        media_type = "application/zip"
        filename = f"{dataset_stem}_edited_{short_id}_{version_label}.zip"
    else:
        content = target_path.read_bytes()
        media_type = "application/octet-stream"
        filename = f"{dataset_stem}_edited_{short_id}_{version_label}.inpx"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        },
    )

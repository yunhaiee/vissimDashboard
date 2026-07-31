"""QUEUE 3 unit 7: the rollback-planning core for ``tools_edit.rollback_last_edit``.

**Persistence-reality map (read this before touching anything below).**
``services.agent.state.ArtifactManager`` already gives every agent edit
session a real, append-only version chain: ``clone()`` copies the active
dataset's .inpx into ``<work_dir>/working.inpx`` once, and every successful
write's ``save_version()`` call snapshots the CURRENT working copy as the
next ``<work_dir>/edit_NNN.inpx`` (numbers 1, 2, 3, ... never reused, never
overwritten -- see that class's own docstring). ``rollback_to(version)``
already exists and already does a byte-identical restore (``version=0`` is
special-cased to mean "the pristine clone", i.e. copy straight from
``source_inpx`` since there is no ``edit_000.inpx``). So "artifact versions
already exist" (docs/phase2/02-ROADMAP.md) is literally true at the FILE
level: nothing new needed there.

What does NOT already exist is any metadata about WHICH tool/object/field
produced a given version, or whether the CURRENT session's most recent
saved version was itself the product of an earlier rollback (needed for the
"one level of undo only" decision below). That is what this module adds, by
reading ``services.agent.session_store``'s already-persisted
``state.json`` (``AgentState.history`` — the same ``StepRecord`` list
every agent turn already appends to and ``orchestrator._finalize_turn``
already writes to disk at the end of EVERY turn). This is metadata
recovery, not a new persistence mechanism: if ``state.json`` is missing or
unreadable (a legacy session, a hand-crafted work dir in a test, or a
mid-write crash before the first save), the FILE-level restore still works
correctly (the version-number chain is the authority for that), but the
rich "which tool/object" description and the "already at the rollback
boundary" safety check both degrade — see :func:`compute_rollback_plan`'s
own docstring for exactly how.

**Design decision: ONE level of undo, not an arbitrary stack.** A naive
"always restore to ``latest_version - 1``" scheme breaks the moment a
rollback has itself happened once: rolling back v2 (edit B) to v1 (edit A)
creates a NEW v3 whose CONTENT equals v1 — so "``latest - 1``" from v3 is
v2, and blindly restoring there would REDO edit B, not undo edit A too.
Building genuine multi-level undo would need a real "predecessor" pointer
per version that nothing in this codebase persists today — inventing one
would be exactly the "fake a stack" the task brief warns against. Instead:
:func:`compute_rollback_plan` looks at the most recent applied write in the
session's history; if THAT write was itself ``rollback_last_edit``, a
second consecutive rollback is refused outright
(:class:`RollbackAlreadyAtBoundaryError`) rather than silently redoing the
first one. A fresh edit after a rollback resets the boundary — the next
rollback call undoes THAT edit normally, one level at a time.

No COM anywhere in this module (file I/O and JSON only) — matches the unit
brief ("edit rollback verb ... No COM required").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.agent import session_store
from services.agent.toolkit import default_registry

_VERSION_RE = re.compile(r"^edit_(\d{3})\.inpx$")


class NothingToRollBackError(RuntimeError):
    """No applied edit exists in this session to roll back.

    Raised (never returned as a dict — see ``tools_edit.py``'s module
    docstring on why every write tool's failure path raises) so
    ``toolkit.dispatch`` reports a genuine ``status: "error"`` envelope. The
    message embeds the literal marker ``rollback_nothing_to_undo`` so it
    stays greppable/assertable, mirroring ``NoEditSessionError``'s
    ``no_edit_session`` convention exactly.
    """


class RollbackTargetNotRecoverableError(RuntimeError):
    """The archived pre-edit snapshot this rollback needs is missing on disk.

    Should not happen in practice — ``ArtifactManager`` never deletes or
    renumbers a saved ``edit_NNN.inpx`` — but a work dir can be tampered
    with (or a version file can vanish under a concurrent, out-of-band
    cleanup) and a refused edit beats a wrong one. Message embeds the
    literal marker ``rollback_target_not_recoverable``.
    """


class RollbackAlreadyAtBoundaryError(RuntimeError):
    """The most recent applied action was itself a rollback.

    This codebase's version chain supports exactly ONE level of undo per
    "chain" (see the module docstring's design-decision section) — a second
    consecutive rollback attempt, with no new edit in between, is refused
    rather than silently redoing the first rollback. Message embeds the
    literal marker ``rollback_already_at_boundary``.
    """


@dataclass(frozen=True)
class RollbackPlan:
    """Everything :func:`~services.agent.tools_edit.rollback_last_edit` needs
    to perform and describe one rollback.

    ``target_version``: the saved version to restore the working copy TO (0
    means the pristine clone, restored from the active dataset's source
    .inpx rather than a numbered snapshot — mirrors
    ``ArtifactManager.rollback_to``'s own ``version=0`` special case).
    ``reverted_from_version``: the version being undone (the session's
    current latest saved version).

    ``undone_tool``/``undone_params``/``undone_verification`` are
    best-effort, recovered from the session's persisted ``state.json``
    history (see the module docstring) — ``None`` when history is missing,
    unreadable, or does not (yet) contain a matching write-tier record. A
    ``None`` here does not block the rollback itself (the file-level restore
    needs only the version numbers), it just means the tool's own message
    and the postwrite diff's dynamic exemption both degrade to a generic,
    unstructured description.

    ``run_since_edit``: ``True`` when a ``run_simulation`` call succeeded
    (tier "run", status "ok") somewhere AFTER the undone edit in history —
    the signal :func:`~services.agent.tools_edit.rollback_last_edit` uses to
    trigger the ``rollback_invalidates_run_results`` warn-tier constraint.
    Always ``False`` when history is unavailable (nothing to check against).

    ``history_available``: whether ``state.json`` was actually readable —
    surfaced so the tool's own message can be honest about a degraded
    (file-only) rollback rather than silently pretending it has full
    context.
    """

    work_dir: Path
    target_version: int
    reverted_from_version: int
    undone_tool: str | None
    undone_params: dict[str, Any] | None
    undone_verification: dict[str, Any] | None
    run_since_edit: bool
    history_available: bool


def list_saved_versions(work_dir: Path) -> list[int]:
    """Every retained ``edit_NNN.inpx`` version number under ``work_dir``,
    ascending. Empty for a session that has cloned but never saved a write.

    Mirrors ``routers/agent_downloads.py``'s ``_session_versions`` exactly
    (same regex, same "file must actually exist" check) — deliberately
    re-implemented here rather than imported: that module is a FastAPI
    router (read-only download surface) this stdlib-only planning module
    has no business depending on, and the pattern is tiny/stable (backed by
    ``ArtifactManager``'s own ``_VERSION_RE``, unlikely to drift).
    """
    versions: list[int] = []
    try:
        entries = list(Path(work_dir).iterdir())
    except OSError:
        return versions
    for path in entries:
        match = _VERSION_RE.match(path.name)
        if match and path.is_file():
            versions.append(int(match.group(1)))
    return sorted(versions)


def version_path(work_dir: Path, version: int) -> Path:
    """The path a saved version ``N >= 1`` lives at — never called for
    ``version == 0`` (the pristine clone has no numbered snapshot file; see
    :class:`RollbackPlan`'s docstring)."""
    return Path(work_dir) / f"edit_{version:03d}.inpx"


def _is_tier(tool_name: Any, tier: str) -> bool:
    if not isinstance(tool_name, str):
        return False
    spec = default_registry.get(tool_name)
    return spec is not None and spec.tier == tier


def _load_history(work_dir: Path) -> list[dict[str, Any]] | None:
    """Best-effort ``state.json`` history reload — ``None`` on ANY failure
    (missing file, unreadable file, a session-id shape ``session_store``'s
    validator rejects). Never raises: a rollback's FILE-level correctness
    must never depend on this succeeding (see the module docstring).
    """
    session_id = Path(work_dir).name
    try:
        state = session_store.load_state(work_dir, session_id)
    except (ValueError, OSError):
        return None
    if state is None:
        return None
    try:
        return [record.to_dict() for record in state.history]
    except Exception:
        return None


def compute_rollback_plan(work_dir: Path) -> RollbackPlan:
    """Determine what one ``rollback_last_edit`` call should do, or raise a
    structured refusal (see the three exception classes above).

    Restore mechanics (``target_version``/``reverted_from_version``) are
    computed PURELY from the on-disk ``edit_NNN.inpx`` chain — the ground
    truth ``ArtifactManager`` itself uses — so they are correct even when
    ``state.json`` cannot be read. Descriptive metadata
    (``undone_tool``/``undone_params``/``run_since_edit``) is read from
    ``state.json`` best-effort on top of that.
    """
    work_dir = Path(work_dir)
    versions = list_saved_versions(work_dir)
    if not versions:
        raise NothingToRollBackError(
            "No applied edit exists in this session to roll back "
            "(error_type: rollback_nothing_to_undo). Nothing has been written to the working "
            "network yet, so there is no prior state to restore."
        )

    latest = versions[-1]
    target_version = latest - 1
    if target_version > 0 and not version_path(work_dir, target_version).exists():
        raise RollbackTargetNotRecoverableError(
            f"The archived pre-edit snapshot (version {target_version}) is missing on disk -- "
            "cannot roll back safely (error_type: rollback_target_not_recoverable). Refusing "
            "rather than guessing at a partial restore."
        )

    history = _load_history(work_dir)
    history_available = history is not None
    undone_tool: str | None = None
    undone_params: dict[str, Any] | None = None
    undone_verification: dict[str, Any] | None = None
    run_since_edit = False

    if history is not None:
        last_write_index: int | None = None
        for index in range(len(history) - 1, -1, -1):
            record = history[index]
            action = record.get("action")
            observation = record.get("observation") or {}
            if _is_tier(action, "write") and observation.get("status") == "ok":
                last_write_index = index
                undone_tool = action
                undone_params = record.get("params") or {}
                undone_verification = observation.get("verification")
                break

        if last_write_index is not None and undone_tool == "rollback_last_edit":
            raise RollbackAlreadyAtBoundaryError(
                "The most recent applied action was itself an undo -- only one level of "
                "rollback is supported per edit (error_type: rollback_already_at_boundary). "
                "Make a new edit before rolling back again."
            )

        if last_write_index is not None:
            for index in range(last_write_index + 1, len(history)):
                record = history[index]
                action = record.get("action")
                observation = record.get("observation") or {}
                if _is_tier(action, "run") and observation.get("status") == "ok":
                    run_since_edit = True
                    break

    return RollbackPlan(
        work_dir=work_dir,
        target_version=target_version,
        reverted_from_version=latest,
        undone_tool=undone_tool,
        undone_params=undone_params,
        undone_verification=undone_verification,
        run_since_edit=run_since_edit,
        history_available=history_available,
    )


def _copy_with_hash(source_path: Path, dest_path: Path) -> str:
    """Atomic copy (same-directory temp file + ``os.replace``, mirroring
    ``VissimEditSession.persist``'s own two-step pattern) of ``source_path``'s
    bytes onto ``dest_path``, so a mid-copy failure can never leave a
    half-written ``dest_path`` on disk. Returns the SHA-256 hex digest of the
    copied bytes. Shared by :func:`restore_working_copy` (target -> working
    copy, unit 7) and :func:`snapshot_working_copy` (working copy -> a
    scratch snapshot, unit 8) — same operation, opposite direction, one
    implementation.
    """
    import hashlib
    import os
    import shutil

    dest_path = Path(dest_path)
    tmp_path = dest_path.with_name(dest_path.stem + "_copy_tmp" + dest_path.suffix)
    shutil.copyfile(source_path, tmp_path)
    digest = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    os.replace(tmp_path, dest_path)
    return digest


def restore_working_copy(working_inpx: Path, target_path: Path) -> str:
    """Copy ``target_path``'s bytes onto ``working_inpx``, byte-for-byte.

    Returns the SHA-256 hex digest of the copied bytes — the caller's own
    reference value for the postwrite verification's ``written_value``/
    ``readback_value`` compare (see ``tools_edit.rollback_last_edit``).
    """
    return _copy_with_hash(target_path, Path(working_inpx))


def snapshot_working_copy(working_inpx: Path, snapshot_path: Path) -> str:
    """QUEUE 3 unit 8 (``apply_edit_plan``): copy ``working_inpx``'s CURRENT
    bytes to ``snapshot_path`` (a scratch file, never matching
    :data:`_VERSION_RE` — never mistaken for a real saved ``edit_NNN.inpx``
    version by :func:`list_saved_versions` or ``ArtifactManager`` — the
    caller is responsible for choosing such a name and deleting it once the
    batch this snapshot exists for has finished, success or failure).

    This is the pre-batch checkpoint ``apply_edit_plan``'s own serial-apply
    loop restores from on a mid-batch failure (via :func:`restore_working_copy`
    in the opposite direction) — deliberately a plain file-level snapshot,
    NOT a new ``ArtifactManager.save_version()`` entry: a batch that succeeds
    must advance the version chain by exactly ONE saved version (the
    orchestrator's own generic post-dispatch ``save_version()`` call already
    does this for any successful write-tier tool), so that
    ``rollback_last_edit``'s existing one-level "latest - 1" mechanism undoes
    the WHOLE batch in a single call — see the QUEUE 3 unit 8 report's
    documented design decision. Returns the SHA-256 hex digest of the
    snapshotted bytes, the reference value ``apply_edit_plan`` compares the
    eventual restore against.
    """
    return _copy_with_hash(Path(working_inpx), Path(snapshot_path))


__all__ = [
    "NothingToRollBackError",
    "RollbackAlreadyAtBoundaryError",
    "RollbackPlan",
    "RollbackTargetNotRecoverableError",
    "compute_rollback_plan",
    "list_saved_versions",
    "restore_working_copy",
    "snapshot_working_copy",
    "version_path",
]

"""Manage the currently selected local Vissim dataset — per workspace.

Unit 1.4 removed the process-wide active-dataset singleton this module used to
be. There is no longer a module-level ``UPLOAD_DIR``/``ACTIVE_INPX_PATH``
constant: every accessor derives its path from the workspace bound to the
CURRENT request/job (``services.workspaces.current_workspace_id``), so two
clients' networks can live on one box without either being able to see the
other's. The public function names, their return types, and their exact
existence semantics are unchanged, so all ~50 call sites across the routers and
services keep working untouched.

Preserved deliberately (it is load-bearing, not an accident):

* The project-db / Scenarios / Modifications accessors gate on the .inpx's
  existence — an uploaded dataset without a ``.vissimpdb`` still reports the
  UPLOADED (missing) path, not the demo one, so nothing silently mixes an
  uploaded network with the demo project's scenarios.
* ``get_active_real_counts_excel`` gates on its OWN existence instead, because
  a validation workbook can be uploaded standalone, with or without a network.
* With nothing uploaded, every accessor falls back to the ``data/`` demo
  defaults from ``config.py``, so a fresh checkout still renders.

Upload safety (unit 1.4's "uploads are the hostile surface" hardening):

* **Stage-then-promote.** An upload is validated and written into
  ``uploads/incoming-<token>/`` and only then renamed into place. The previous
  dataset is destroyed only after the new one is complete on disk — the old
  ``rmtree``-then-write sequence had a window where a malformed zip left the
  user with NO dataset at all. Every failure path — including a failure of the
  promote itself — restores the previous dataset and removes its own
  scratch directories; see :func:`_staged_upload` for the exact ordering.
* **A storage failure is a 4xx, not a 500.** Windows hands out
  ``PermissionError``/``FileExistsError`` whenever anything (Vissim, a results
  reader, an explorer window) holds ``uploads/current`` open, and that is
  NORMAL operation for this app, not an exotic error. Those surface as
  :class:`DatasetStorageError` and the router answers 409 with "close whatever
  has the model open", never an unhandled 500.
* **One upload at a time per workspace.** :data:`_UPLOAD_LOCKS` serializes
  stage-then-promote per workspace; concurrent uploads into ONE workspace used
  to collide on the rename pair (measured: 8 threads → 6 exceptions and 5
  leftover directories). Different workspaces still upload in parallel.
* **Zip-bomb refusal**, by three guards that stop three different things —
  named precisely here because the previous comment credited the wrong one:
  :func:`_guard_zip_bomb` refuses an archive whose DECLARED member count or
  total size already exceeds :data:`MAX_ZIP_MEMBERS` /
  :data:`MAX_ZIP_TOTAL_BYTES`, before a byte is read; :func:`_read_member`
  streams each member and stops on the bytes actually produced, so ONE member
  cannot outrun the budget (and ``zipfile`` independently stops at the
  declared ``file_size`` and fails the CRC, which is what turns a lying header
  into a 400); :class:`_ExtractBudget` carries the running total across
  members, including a member extracted twice.
* **Cache invalidation.** Every mutator clears ``services.validation``'s
  ``lru_cache``es. Those are keyed on the workbook PATH string, which is stable
  within a workspace — without this, uploading workbook B showed workbook A's
  numbers on the validation dashboard.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
import errno
import hashlib
import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Iterator
from uuid import uuid4
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZipInfo

from config import INPX_PATH, MODIFICATIONS_DIR, PROJECT_DB_PATH, REAL_COUNTS_EXCEL, SCENARIOS_DIR
from services import validation as _validation
from services import workspaces

logger = logging.getLogger(__name__)

#: Zip-bomb guards. 10,000 members and 2 GiB decompressed comfortably clear
#: every real Vissim project drop seen so far (the Daejeon drop is ~3 MB across
#: a few dozen files) while refusing an archive that would fill the disk.
MAX_ZIP_MEMBERS = 10_000
MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

#: Scratch-directory prefixes stage-then-promote creates directly under a
#: workspace's ``uploads/``. Swept on every upload; nothing else in that
#: directory (``current``, ``staging``, ``mapping_memory.json``) matches.
_LEFTOVER_PREFIXES = ("incoming-", "retired-")

#: Upper bound on how many leftovers ONE sweep will remove, so a pathological
#: uploads directory can never turn a single request into an unbounded
#: filesystem walk. Anything beyond this is picked up by the next upload.
MAX_SWEEP_ENTRIES = 64


class DatasetStorageError(RuntimeError):
    """The dataset could not be written/replaced for a FILESYSTEM reason.

    Distinct from ``ValueError`` (which means "the uploaded bytes are bad" and
    maps to 400): the upload was fine, the disk said no. Raised only from paths
    that have already guaranteed the previous dataset is intact, so the router
    can answer 409 with "nothing changed, try again" and mean it.
    """


def _is_file_lock_error(exc: BaseException) -> bool:
    """Whether ``exc`` is the "something has this open / already there" family.

    Windows is the reason this exists: an open handle on
    ``uploads/current/current.inpx`` (Vissim, a results reader, a COM session,
    even an explorer window sitting in the folder) makes the directory rename
    fail with ``WinError 5``/``32``/``33``, and a process whose CWD is inside
    it fails with ``WinError 32``. All of those are user-fixable, so they get a
    user-fixable message instead of a generic one.
    """
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in {5, 32, 33, 145, 183}:
        return True
    return exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY, errno.EEXIST}


def _storage_failure_message(exc: BaseException) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    if _is_file_lock_error(exc):
        return (
            "Could not replace the active dataset because something on this machine still "
            "has the current model open (Vissim, a running simulation, or a window sitting "
            "in the dataset folder). Close it and upload again — the previous dataset was "
            f"left unchanged. [{detail}]"
        )
    return (
        "Could not replace the active dataset because of a storage error. The previous "
        f"dataset was left unchanged. [{detail}]"
    )


# ---------------------------------------------------------------------------
# Workspace-scoped path derivation (replaces the old module-level constants)
# ---------------------------------------------------------------------------


def uploads_root() -> Path:
    """``<workspace>/uploads`` — the parent every upload artifact lives under.

    Staging and promotion both happen inside this one directory so the
    promote step is a same-filesystem rename, never a cross-device copy.
    """
    return workspaces.workspace_uploads_root(workspaces.current_workspace_id())


def upload_dir() -> Path:
    return uploads_root() / "current"


def active_inpx_path() -> Path:
    return upload_dir() / "current.inpx"


def active_project_db_path() -> Path:
    return upload_dir() / "project.vissimpdb"


def active_scenarios_dir() -> Path:
    return upload_dir() / "Scenarios"


def active_modifications_dir() -> Path:
    return upload_dir() / "Modifications"


def active_real_counts_excel() -> Path:
    return upload_dir() / "real_counts.xlsx"


def metadata_path() -> Path:
    return upload_dir() / "metadata.json"


# ---------------------------------------------------------------------------
# Accessors (unchanged signatures/semantics — see the module docstring)
# ---------------------------------------------------------------------------


def get_active_inpx_path() -> Path:
    uploaded = active_inpx_path()
    return uploaded if uploaded.exists() else INPX_PATH


def get_active_project_db_path() -> Path:
    return active_project_db_path() if active_inpx_path().exists() else PROJECT_DB_PATH


def get_active_scenarios_dir() -> Path:
    return active_scenarios_dir() if active_inpx_path().exists() else SCENARIOS_DIR


def get_active_modifications_dir() -> Path:
    return active_modifications_dir() if active_inpx_path().exists() else MODIFICATIONS_DIR


def get_active_real_counts_excel() -> Path:
    uploaded = active_real_counts_excel()
    return uploaded if uploaded.exists() else REAL_COUNTS_EXCEL


def get_active_dataset() -> dict:
    workspace_id = workspaces.current_workspace_id()
    inpx = active_inpx_path()

    if not inpx.exists():
        return _with_environment_warnings({
            "source": "default",
            "kind": "default",
            "filename": INPX_PATH.name,
            "path": str(INPX_PATH),
            "uploaded_at": None,
            "revision": _revision_for_paths(
                [INPX_PATH, PROJECT_DB_PATH, SCENARIOS_DIR, active_real_counts_excel()]
            ),
            "has_project_db": PROJECT_DB_PATH.exists(),
            "has_scenarios": SCENARIOS_DIR.exists(),
            "has_modifications": MODIFICATIONS_DIR.exists(),
            "has_real_counts": get_active_real_counts_excel().exists(),
            "workspace_id": workspace_id,
        })

    metadata = _read_metadata()
    return _with_environment_warnings({
        "source": "uploaded",
        "kind": metadata.get("kind") or "inpx",
        "filename": metadata.get("filename") or inpx.name,
        "path": str(inpx),
        "uploaded_at": metadata.get("uploaded_at"),
        "revision": _revision_for_paths(
            [
                inpx,
                active_project_db_path(),
                active_scenarios_dir(),
                active_modifications_dir(),
                active_real_counts_excel(),
            ]
        ),
        "has_project_db": active_project_db_path().exists(),
        "has_scenarios": active_scenarios_dir().exists(),
        "has_modifications": active_modifications_dir().exists(),
        "has_real_counts": get_active_real_counts_excel().exists(),
        "workspace_id": workspace_id,
    })


def _with_environment_warnings(payload: dict) -> dict:
    """Attach any box-level condition that explains what this payload SHOWS.

    Today there is exactly one: an incomplete legacy migration
    (``workspaces.legacy_migration_status``), which makes a box with a real
    dataset in ``runtime/uploads`` report ``source: "default"`` and render the
    ``data/`` demo network. That used to be entirely silent — one WARNING on a
    boot nobody watched — and this payload is the one place both
    ``GET /api/datasets/active`` and the project banner already read.

    The key is present ONLY when something is wrong, so every healthy response
    is byte-identical to before this existed and no client has to change.
    """
    status = workspaces.legacy_migration_status()
    if status:
        payload["warnings"] = [status]
    return payload


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------


def save_active_dataset(filename: str, content: bytes) -> dict:
    """Persist an uploaded INPX or zipped Vissim project as the active dataset.

    The WHOLE call — validate, stage, promote, and the read-back that builds
    the response — holds the workspace's (reentrant) upload lock, not just the
    promote. The read-back is not incidental: ``get_active_dataset`` walks
    ``uploads/current`` to compute the revision, and on Windows a directory
    ENUMERATION holds a handle that makes another thread's promote rename fail
    with ``ACCESS_DENIED``. Measured — with only the promote locked, eight
    concurrent uploads still failed each other on their own response-building
    step.
    """
    lowered = filename.lower()
    if not lowered.endswith((".inpx", ".zip")):
        # Checked before touching the workspace, so a bad extension is a plain
        # 400 that never creates a directory.
        raise ValueError("Only .inpx and .zip files are supported.")
    with _upload_lock(uploads_root()):
        if lowered.endswith(".inpx"):
            return _save_single_inpx(filename, content)
        return _save_zip_dataset(filename, content)


def reset_active_dataset() -> dict:
    """Drop this workspace's uploaded dataset, falling back to the ``data/`` demo.

    Holds the same per-workspace upload lock as stage-then-promote — a reset
    racing an upload would otherwise ``rmtree`` a directory the promote is in
    the middle of renaming aside — and reports an undeletable directory as a
    :class:`DatasetStorageError`. A Windows handle on ``current.inpx`` used to
    surface here as an unhandled 500, exactly as it did on the upload path.
    """
    base = uploads_root()
    with _upload_lock(base):
        target = upload_dir()
        if target.exists():
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise DatasetStorageError(
                    "Could not remove the active dataset"
                    + (
                        " because something on this machine still has it open (Vissim, a "
                        "running simulation, or a window sitting in the dataset folder). "
                        "Close it and try again."
                        if _is_file_lock_error(exc)
                        else " because of a storage error."
                    )
                    + f" [{type(exc).__name__}: {exc}]"
                ) from exc
        _sweep_upload_leftovers(base)
    _clear_validation_caches()
    return get_active_dataset()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def reset_active_dataset_if_sha(expected_sha256: str) -> dict:
    """Reset the active dataset to the ``data/`` demo, but ONLY while the active
    ``.inpx`` still hashes to ``expected_sha256`` — atomically.

    This is the identity-guarded delete ``genesis_jobs._revoke_publication``
    needs. The bug it closes is a TOCTOU: a caller that reads the active hash,
    finds it matches, and THEN calls :func:`reset_active_dataset` can have a
    concurrent upload land in the gap and destroy a dataset that never matched.
    Here the hash re-check and the ``rmtree`` happen under the SAME per-workspace
    upload lock the upload/reset paths take, so nothing can be swapped in
    between: an active dataset that is no longer this artifact is a recorded
    no-op, never a delete.

    Returns ``{"removed": bool, "active_sha256": str, "reason": str}``. Raises
    :class:`DatasetStorageError` only when the matching dataset could not be
    removed (a held Windows handle), exactly like :func:`reset_active_dataset`.
    """
    base = uploads_root()
    with _upload_lock(base):
        active = active_inpx_path()
        active_sha = _sha256_file(active) if active.is_file() else ""
        if not expected_sha256 or active_sha != expected_sha256:
            # Re-checked UNDER the lock: whatever is active now is not the
            # artifact we were asked to revoke, so we touch nothing.
            return {
                "removed": False,
                "active_sha256": active_sha,
                "reason": "active_dataset_is_not_this_artifact",
            }
        target = upload_dir()
        if target.exists():
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise DatasetStorageError(
                    "Could not remove the active dataset"
                    + (
                        " because something on this machine still has it open (Vissim, a "
                        "running simulation, or a window sitting in the dataset folder). "
                        "Close it and try again."
                        if _is_file_lock_error(exc)
                        else " because of a storage error."
                    )
                    + f" [{type(exc).__name__}: {exc}]"
                ) from exc
        _sweep_upload_leftovers(base)
    _clear_validation_caches()
    return {"removed": True, "active_sha256": active_sha, "reason": "matched"}


def save_active_real_counts_excel(filename: str, content: bytes) -> dict:
    """Persist a standalone validation workbook without replacing the active network."""
    if not content.strip():
        raise ValueError("Uploaded workbook is empty.")
    if not filename.lower().endswith(".xlsx"):
        raise ValueError("Only .xlsx validation workbooks are supported.")

    # Same lock as the upload path: this writes INTO ``uploads/current``, which
    # a concurrent promote renames out from under it.
    with _upload_lock(uploads_root()):
        upload_dir().mkdir(parents=True, exist_ok=True)
        active_real_counts_excel().write_bytes(content)

        metadata = _read_metadata()
        metadata.update(
            {
                "real_counts_filename": Path(filename).name,
                "real_counts_uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write_metadata_payload(metadata)
    _clear_validation_caches()
    return get_active_dataset()


def clear_active_real_counts_excel() -> dict:
    """Remove only the standalone validation workbook, preserving the active network."""
    with _upload_lock(uploads_root()):
        active_real_counts_excel().unlink(missing_ok=True)

        metadata = _read_metadata()
        metadata.pop("real_counts_filename", None)
        metadata.pop("real_counts_uploaded_at", None)
        _write_metadata_payload(metadata)
    _clear_validation_caches()
    return get_active_dataset()


# ---------------------------------------------------------------------------
# Per-dataset georeference — U-1-map (docs/ux-backlog.md, elaboration item #7)
# ---------------------------------------------------------------------------
#
# THE BUG THIS CLOSES, reported from live use: the dashboard MAP was dead for
# every genesis-published dataset while the 도식 (schematic) worked. The map is
# ``routers/geojson.py``, and it is pinned to the SouthSt Helmert constants —
# five numbers fitted to nine surveyed Murdoch intersections — plus a
# ``refPointMap``/``refPointNet`` anchor read out of the .inpx. A genesis
# network has neither: ``services/genesis/emit.py`` deliberately writes NO
# refPointMap (it makes no absolute claim), so the transform load 500s, and even
# if it had not, Perth's constants would place a Daejeon network in the Indian
# Ocean.
#
# What a genesis network DOES have is the exact plane it was built on. This pair
# carries that plane — origin latitude/longitude and the projection kind — from
# the publish step to the map, per dataset, so the map transforms each dataset
# with its OWN transform instead of one project's constants applied to
# everybody's geometry. A dataset with no such record (every consultant upload)
# is untouched and keeps the Helmert path byte for byte.

#: Where the record lives inside ``uploads/current/metadata.json``. One key, so
#: a dataset either carries a per-dataset transform or does not — there is no
#: half-recorded state a reader would have to interpret.
GEOREFERENCE_METADATA_KEY = "georeference"


def save_active_dataset_with_georeference(
    filename: str, content: bytes, georeference: dict | None
) -> dict:
    """Promote a dataset AND record its transform as ONE indivisible step.

    THE RACE THIS CLOSES, which is not theoretical on a box where two people can
    publish from two browsers. :func:`save_active_dataset` and
    :func:`set_active_dataset_georeference` each take the per-workspace upload
    lock, but as two SEPARATE acquisitions — so between them the lock is free,
    and a second registration can slip its whole promote into that window. The
    outcome is the worst kind available here: dataset B's bytes sitting under
    dataset A's projection origin, with both writes individually "successful"
    and nothing anywhere recording that the pair disagree. Every coordinate the
    dashboard map then draws is confidently, silently in the wrong place.

    Holding ONE lock across both writes makes that interleaving impossible. The
    lock is the same reentrant per-workspace lock both callees take
    (``_upload_lock`` returns an ``RLock``), so their own ``with`` blocks
    re-enter it rather than deadlocking — which is why this is a wrapper and not
    a reimplementation of either.

    ``georeference=None`` clears any previous record, and that is load-bearing:
    it is what stops a dataset this app cannot place from inheriting the
    plane of the one it replaced.
    """
    with _upload_lock(uploads_root()):
        stored = save_active_dataset(filename, content)
        set_active_dataset_georeference(georeference)
        return stored


def set_active_dataset_georeference(georeference: dict | None) -> None:
    """Record (or clear) the transform that places THIS dataset on the earth.

    Written under the same per-workspace upload lock every other metadata write
    takes, and read back through ``_read_metadata`` so it merges with whatever
    the promote already wrote rather than replacing the descriptor.

    ``None`` clears it, which is what a caller publishing a dataset it cannot
    georeference must do: a stale record from the PREVIOUS dataset would be
    worse than none at all — it would draw the new network in the old one's
    place with no sign anything was wrong.
    """
    with _upload_lock(uploads_root()):
        metadata = _read_metadata()
        if georeference is None:
            metadata.pop(GEOREFERENCE_METADATA_KEY, None)
        else:
            metadata[GEOREFERENCE_METADATA_KEY] = georeference
        _write_metadata_payload(metadata)


def get_active_dataset_georeference() -> dict | None:
    """This dataset's own transform, or ``None`` for one that has no record.

    ``None`` is the answer for every dataset uploaded through the browser and
    for the packaged ``data/`` demo, and it means exactly one thing to the
    caller: use whatever transform you used before this function existed. It is
    never a reason to invent one.
    """
    record = _read_metadata().get(GEOREFERENCE_METADATA_KEY)
    return record if isinstance(record, dict) else None


def _clear_validation_caches() -> None:
    """Drop ``services.validation``'s ``lru_cache``es after any dataset write.

    They are keyed on the workbook PATH STRING only, and within one workspace
    that string never changes — so without this, replacing the dataset left the
    validation dashboard serving the PREVIOUS upload's numbers (measured, not
    theoretical). Best-effort: a cache-clear failure must never turn a
    successful upload into a 500.
    """
    try:
        _validation.clear_validation_caches()
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("datasets: could not clear validation caches", exc_info=True)


# ---------------------------------------------------------------------------
# Stage-then-promote
# ---------------------------------------------------------------------------


#: One lock per workspace uploads root, so two uploads into ONE workspace
#: cannot interleave their rename pair while two DIFFERENT workspaces still
#: upload concurrently. Keyed by the uploads-root path string rather than the
#: workspace id because tests repoint ``WORKSPACES_ROOT`` — the path is what
#: actually collides. Reentrant, because :func:`save_active_dataset` takes it
#: for the whole operation and :func:`_staged_upload` takes it again inside.
#: In-process only, exactly like ``services.workspaces._REGISTRY_LOCK``:
#: file-based storage, single-instance app. Entries are never evicted; one
#: ``RLock`` per workspace ever seen is bounded by the number of workspaces,
#: not by request volume.
_UPLOAD_LOCKS: dict[str, threading.RLock] = {}
_UPLOAD_LOCKS_GUARD = threading.Lock()

#: Windows raises a sharing violation whenever ANY reader holds a handle under
#: ``uploads/current`` — including a plain directory enumeration, which is what
#: ``get_active_dataset``'s revision walk does on every ``/api/datasets/active``
#: poll. Those handles live for microseconds, so a few short retries turn a
#: spurious refusal into a successful upload, while a genuinely held model
#: (Vissim has it open) outlives all of them and still reports cleanly. Total
#: added latency in the worst case: ~200 ms, and only on a failing upload.
_RENAME_RETRIES = 5
_RENAME_RETRY_DELAY_SECONDS = 0.05


def _upload_lock(base: Path) -> threading.RLock:
    key = str(base)
    with _UPLOAD_LOCKS_GUARD:
        lock = _UPLOAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _UPLOAD_LOCKS[key] = lock
        return lock


def _rename_with_retry(source: Path, target: Path) -> None:
    """``source.rename(target)``, retrying only the transient-lock family.

    Anything that is not a lock-class error (and anything still locked after
    the last attempt) propagates unchanged — the callers depend on seeing the
    real exception to decide whether to restore.
    """
    for attempt in range(_RENAME_RETRIES):
        try:
            source.rename(target)
            return
        except OSError as exc:
            if attempt == _RENAME_RETRIES - 1 or not _is_file_lock_error(exc):
                raise
            time.sleep(_RENAME_RETRY_DELAY_SECONDS)


def _sweep_upload_leftovers(base: Path, keep: Path | None = None) -> None:
    """Remove stale ``incoming-*``/``retired-*`` directories under ONE workspace.

    Bounded three ways, deliberately: it lists exactly ``base`` (never
    recursing into sibling workspaces or into ``current``), it only ever
    removes direct children whose name carries one of
    :data:`_LEFTOVER_PREFIXES`, and it stops after :data:`MAX_SWEEP_ENTRIES`.
    Callers hold the workspace's upload lock, so nothing it sees can be an
    in-flight staging directory of this process; ``keep`` is belt-and-braces
    for the post-promote call.

    Best-effort by contract: a leftover we cannot delete (still-open handle)
    must never fail an otherwise successful upload — the next upload retries.

    ONE thing is never swept: a ``retired-*`` directory while ``current`` does
    not exist. That is the signature of a promote whose restore also failed, so
    the ``retired-*`` copy is the user's ONLY dataset. Deleting it would turn a
    recoverable "rename this back" into permanent data loss.
    """
    try:
        children = sorted(base.iterdir())
    except OSError:
        return
    have_current = (base / "current").exists()
    removed = 0
    for child in children:
        if removed >= MAX_SWEEP_ENTRIES:
            logger.warning(
                "datasets: more than %d upload leftovers under %s; sweeping the rest on the "
                "next upload",
                MAX_SWEEP_ENTRIES,
                base,
            )
            break
        if keep is not None and child == keep:
            continue
        if not child.name.startswith(_LEFTOVER_PREFIXES):
            continue
        if not child.is_dir():
            continue
        if child.name.startswith("retired-") and not have_current:
            logger.warning(
                "datasets: keeping %s - this workspace has no uploads/current, so that "
                "directory may hold the only copy of the dataset; rename it back by hand",
                child,
            )
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    if removed:
        logger.info("datasets: swept %d stale upload leftover(s) under %s", removed, base)


@contextmanager
def _staged_upload() -> Iterator[Path]:
    """Yield a fresh ``uploads/incoming-<token>/`` directory, then promote it.

    ANY exception — from the caller's block (a malformed zip, a zip bomb, a
    disk error) *or from the promote itself* — discards the staging directory
    and leaves the previously active dataset byte-identical. The promote is
    INSIDE the guard on purpose: it used to be the statement after the ``try``,
    so a promote failure (an open handle on ``current/current.inpx`` is enough,
    and this app opens that file routinely) orphaned ``incoming-<token>/``
    forever — nothing anywhere swept them.

    The whole body runs under the workspace's upload lock, and both a leading
    and a trailing sweep run inside it, so a crashed earlier attempt's
    leftovers are collected even if this process never saw it.
    """
    base = uploads_root()
    base.mkdir(parents=True, exist_ok=True)
    with _upload_lock(base):
        _sweep_upload_leftovers(base)
        staged = base / f"incoming-{uuid4().hex[:12]}"
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True)
        try:
            yield staged
            _promote_staged_upload(staged)
        except BaseException:
            # ``staged`` no longer exists once the promote renamed it into
            # place, so this is a no-op on the success path it cannot reach.
            shutil.rmtree(staged, ignore_errors=True)
            raise
        _sweep_upload_leftovers(base, keep=upload_dir())


def _promote_staged_upload(staged: Path) -> None:
    """Swap ``staged`` in as ``uploads/current``: rename the old one aside,
    rename the new one in, then delete the old one.

    Two failure points, both of which MUST end with the previous dataset in
    place — this function is the entire reason stage-then-promote exists:

    * The first rename (``current`` → ``retired-*``) fails when something holds
      the current dataset open. Nothing has moved yet, so it is reported as a
      :class:`DatasetStorageError` and the upload simply did not take.
    * The second rename (``staged`` → ``current``) fails with the previous
      dataset sitting under ``retired-*``. It is renamed straight back. The
      guard is ``except BaseException`` and not ``except OSError``: with
      ``OSError`` alone, ANY other exception type skipped the restore entirely
      and the workspace was left with NO dataset at all (measured:
      ``current.inpx`` gone, the API flipping to ``source: "default"``, two
      leftover directories). Restore first, then re-raise.
    """
    current = upload_dir()
    retired = current.parent / f"retired-{uuid4().hex[:12]}"
    retired_previous = False
    if current.exists():
        try:
            _rename_with_retry(current, retired)
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise DatasetStorageError(_storage_failure_message(exc)) from exc
            raise
        retired_previous = True
    try:
        _rename_with_retry(staged, current)
    except BaseException as exc:
        if retired_previous:
            _restore_retired(retired, current)
        shutil.rmtree(staged, ignore_errors=True)
        if isinstance(exc, Exception):
            raise DatasetStorageError(_storage_failure_message(exc)) from exc
        raise
    if retired_previous:
        shutil.rmtree(retired, ignore_errors=True)


def _restore_retired(retired: Path, current: Path) -> None:
    """Put the previous dataset back after a failed promote.

    Never raises: it runs while another exception is already in flight, and
    masking that one with a restore failure would hide the real cause. If even
    the restore fails, the dataset is still ON DISK under ``retired-*`` — the
    CRITICAL log names the exact directory a human can rename back, and the
    sweep deliberately leaves it alone until it succeeds (a sweep only runs
    after a *successful* promote or at the start of the next upload, by which
    point ``current`` is authoritative again).
    """
    try:
        _rename_with_retry(retired, current)
    except BaseException:  # noqa: BLE001 - see the docstring
        logger.critical(
            "datasets: could not restore the previous dataset from %s to %s after a failed "
            "promote; the data is intact at %s and must be renamed back by hand",
            retired,
            current,
            retired,
            exc_info=True,
        )


def _save_single_inpx(filename: str, content: bytes) -> dict:
    if not content.strip():
        raise ValueError("Uploaded file is empty.")
    _validate_inpx(content)
    with _staged_upload() as staged:
        (staged / "current.inpx").write_bytes(content)
        _write_metadata_into(staged, filename, "inpx")
    _clear_validation_caches()
    return get_active_dataset()


def _save_zip_dataset(filename: str, content: bytes) -> dict:
    if not content.strip():
        raise ValueError("Uploaded file is empty.")

    try:
        with ZipFile(BytesIO(content)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            _guard_zip_bomb(members)
            inpx_member = _find_first_member(members, ".inpx")
            project_member = _find_first_member(members, ".vissimpdb")
            real_counts_member = _find_named_member(members, "real_counts.xlsx")

            if inpx_member is None:
                raise ValueError("Zip dataset must contain an .inpx file.")

            budget = _ExtractBudget()
            inpx_bytes = _read_member(archive, inpx_member, budget)
            _validate_inpx(inpx_bytes)

            with _staged_upload() as staged:
                (staged / "current.inpx").write_bytes(inpx_bytes)

                if project_member is not None:
                    (staged / "project.vissimpdb").write_bytes(
                        _read_member(archive, project_member, budget)
                    )

                _extract_tree_from_zip(archive, members, "Scenarios", staged / "Scenarios", budget)
                _extract_tree_from_zip(
                    archive, members, "Modifications", staged / "Modifications", budget
                )

                if real_counts_member is not None:
                    (staged / "real_counts.xlsx").write_bytes(
                        _read_member(archive, real_counts_member, budget)
                    )

                _write_metadata_into(staged, filename, "zip")
    except BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid zip archive.") from exc

    _clear_validation_caches()
    return get_active_dataset()


class _ExtractBudget:
    """Cumulative cap on the bytes ONE archive is allowed to produce.

    Precisely what bounds what (this docstring used to credit the wrong
    component, which is how a guard quietly rots):

    * **A single member** is bounded by :func:`_read_member`, which streams the
      member and stops the moment the bytes actually produced exceed what this
      budget has left — and, independently of us, by ``zipfile`` itself:
      ``ZipExtFile`` stops at the DECLARED ``file_size`` and then verifies the
      CRC, so a member whose header lies about its size raises ``BadZipFile``
      (caught in :func:`_save_zip_dataset` → 400).
    * **The total across members** is what THIS class bounds, and it is the
      second cumulative guard, not the first: :func:`_guard_zip_bomb` has
      already refused any archive whose TOTAL declared size exceeds the cap
      before a byte is read. What this adds on top is per-EXTRACTION
      accounting, which differs from that precheck whenever a member is read
      more than once — the ``.inpx`` matched by :func:`_find_first_member` can
      also sit under ``Scenarios/`` and be extracted a second time, and only
      this budget charges it twice.
    * :meth:`spend` charges the bytes actually produced. Because zipfile
      truncates at the declared size, actual <= declared, so it is defence in
      depth: it fires only if that ever stops being true. :func:`_read_member`
      does not rely on it — it enforces :meth:`remaining` itself, mid-stream.
    """

    def __init__(self, limit: int | None = None) -> None:
        # Read from the module constant at CONSTRUCTION, never bound as a
        # default argument at import time -- tests shrink the cap by
        # monkeypatching the constant.
        self.limit = MAX_ZIP_TOTAL_BYTES if limit is None else limit
        self.used = 0

    def remaining(self) -> int:
        return self.limit - self.used

    def reserve(self, declared: int) -> None:
        if declared > self.remaining():
            raise ValueError(_zip_too_large_message())

    def spend(self, actual: int) -> None:
        self.used += actual
        if self.used > self.limit:
            raise ValueError(_zip_too_large_message())


def _zip_too_large_message() -> str:
    return (
        "Zip dataset is too large to extract safely "
        f"(over {MAX_ZIP_TOTAL_BYTES // (1024 * 1024)} MB decompressed). "
        "The previous dataset was left unchanged."
    )


def _guard_zip_bomb(members: list[ZipInfo]) -> None:
    """Refuse an archive whose DECLARED contents already exceed the caps.

    Checked before a single byte is extracted, so the previous dataset is
    never even staged over, let alone replaced.
    """
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError(
            f"Zip dataset has too many entries ({len(members)}; the limit is {MAX_ZIP_MEMBERS}). "
            "The previous dataset was left unchanged."
        )
    total_declared = sum(max(int(member.file_size or 0), 0) for member in members)
    if total_declared > MAX_ZIP_TOTAL_BYTES:
        raise ValueError(_zip_too_large_message())


#: Streaming granularity for member extraction. Big enough that a real project
#: drop (a few MB) comes out in a handful of reads, small enough that the
#: overshoot past the cap before we notice is bounded by one chunk.
_MEMBER_CHUNK_BYTES = 1024 * 1024


def _read_member(archive: ZipFile, member: ZipInfo, budget: _ExtractBudget) -> bytes:
    """Decompress one member, never producing more than the budget has left.

    Streams via ``archive.open`` rather than ``archive.read`` so the cap is
    OURS: ``archive.read`` fully materialises the member first and only then
    lets :meth:`_ExtractBudget.spend` object, which means a per-member bound
    that exists only because CPython's ``ZipExtFile`` happens to stop at the
    declared ``file_size``. The loop below stops on the bytes actually
    produced, so the guard survives that implementation detail changing.

    Note this does NOT weaken the CRC check, which is what actually catches a
    lying header: measured on a member declaring 34 bytes over 209 MB of real
    data, ``ZipExtFile`` stops at the 34 it was promised and raises
    ``BadZipFile: Bad CRC-32`` inside the FIRST ``read()`` — zero bytes ever
    reach this function, and :func:`_save_zip_dataset` turns it into a 400.
    """
    budget.reserve(max(int(member.file_size or 0), 0))
    allowance = budget.remaining()
    chunks: list[bytes] = []
    produced = 0
    with archive.open(member) as stream:
        while True:
            chunk = stream.read(_MEMBER_CHUNK_BYTES)
            if not chunk:
                break
            produced += len(chunk)
            if produced > allowance:
                raise ValueError(_zip_too_large_message())
            chunks.append(chunk)
    budget.spend(produced)
    return b"".join(chunks)


def _validate_inpx(content: bytes) -> None:
    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("Uploaded file is not a valid INPX/XML file.") from exc


def _find_first_member(members: list, suffix: str):
    for member in members:
        if _safe_member_name(member.filename).lower().endswith(suffix):
            return member
    return None


def _find_named_member(members: list, filename: str):
    target = filename.lower()
    for member in members:
        if Path(_safe_member_name(member.filename)).name.lower() == target:
            return member
    return None


def _extract_tree_from_zip(
    archive: ZipFile,
    members: list,
    folder_name: str,
    destination: Path,
    budget: _ExtractBudget | None = None,
) -> None:
    budget = budget if budget is not None else _ExtractBudget()
    folder_lower = folder_name.lower()
    selected: list[tuple[object, Path]] = []
    for member in members:
        safe_name = _safe_member_name(member.filename)
        parts = Path(safe_name).parts
        lower_parts = [part.lower() for part in parts]
        if folder_lower not in lower_parts:
            continue
        folder_index = lower_parts.index(folder_lower)
        relative = Path(*parts[folder_index + 1 :])
        if not str(relative):
            continue
        selected.append((member, relative))

    if not selected:
        return

    destination.mkdir(parents=True, exist_ok=True)
    for member, relative in selected:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_read_member(archive, member, budget))


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
    return str(Path(*parts)) if parts else ""


def _write_metadata_into(directory: Path, filename: str, kind: str) -> None:
    """Write the descriptor into a STAGED directory, before promotion — so the
    promote rename is the single moment the dataset and its metadata both
    become visible."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.json").write_text(
        json.dumps(_metadata_payload(filename, kind), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _metadata_payload(filename: str, kind: str) -> dict:
    return {
        "filename": Path(filename).name,
        "kind": kind,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_metadata_payload(metadata: dict) -> None:
    upload_dir().mkdir(parents=True, exist_ok=True)
    metadata_path().write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_metadata() -> dict:
    path = metadata_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _revision_for_paths(paths: list[Path]) -> str:
    chunks: list[str] = []
    for path in paths:
        if path.is_dir():
            chunks.append(_directory_revision(path))
        else:
            chunks.append(_file_revision(path))
    return "|".join(chunks)


def _file_revision(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return f"{path.name}:missing"
    return f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}"


def _directory_revision(path: Path) -> str:
    if not path.exists():
        return f"{path.name}:missing"
    latest = 0
    total_size = 0
    count = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        latest = max(latest, stat.st_mtime_ns)
        total_size += stat.st_size
        count += 1
    return f"{path.name}:{latest}:{total_size}:{count}"

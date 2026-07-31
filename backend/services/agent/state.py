"""In-memory agent session state, JSON persistence, and versioned .inpx artifacts.

``AgentState`` is the ⟨goal, artifacts, history, context⟩ tuple described in
docs/phase2/01-ARCHITECTURE.md §2. It holds the *summarized* in-memory history
of an agent session as ``StepRecord`` entries; the full step-by-step event log
is trace.py's job (``backend/services/agent/trace.py``) and is not duplicated
here.

``ArtifactManager`` owns the on-disk lifecycle of the session's working copy
of the active dataset's .inpx file: it clones the source once, then snapshots
every save as an immutable, monotonically-numbered ``edit_NNN.inpx`` version,
reparsing the shadow model (via
``services.agent.tools_network.build_shadow_model`` — the M2.2 seam closure:
the same EXTENDED ``{links, nodes, vehicle_inputs, signals}`` shape
``constraints.py``'s checks expect, not the bare ``inpx_parser.parse_network``
output) after every save and rollback. The user's source dataset is
read-only input and is never written to.

Two M2.2 deferred-finding closures live here (docs/phase2/DECISIONS.md, "M2.2"
bullet):

- **Defensive copying (resolved as COPY).** ``build_shadow_model`` is
  ``lru_cache``-backed (mtime-keyed), so its return value is a dict SHARED
  across every caller hitting the same (path, mtime) — including other
  ``ArtifactManager`` instances and any ``tools_network`` read tool pointed
  at the same file. The :attr:`ArtifactManager.shadow_model` property never
  hands out that shared reference: every access returns an independent
  ``copy.deepcopy``.
- **Clone-precondition guard.** ``clone()`` refuses (``RuntimeError``) to
  overwrite a working copy that has diverged from its latest saved version,
  or that was never saved at all — see
  ``ArtifactManager._guard_against_clobbering_unsaved_work`` for exactly what
  counts as "diverged." Re-cloning over a working copy that is still
  byte-identical to its latest saved version (a fresh process resuming an
  existing session) remains safe and allowed.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.agent.tools_network import build_shadow_model

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^edit_(\d{3})\.inpx$")

#: Extensions verified (live, against PTV Vissim 2026's bundled example
#: networks -- Border Crossing Breitenau.DE, Urban Intersection Beijing.CN,
#: 3D - Complex Intersection Karlsruhe.DE, BRT Priority Texas.US, Roundabout
#: Billings.US) to sit as DIRECT file siblings of an .inpx, and, for at least
#: .vap, to be load-blocking when missing -- docs/phase2/DECISIONS.md's v2
#: unit ⑤ follow-up: "load_check correctly reports init_ok=False ('Import
#: file ... not found') ... a real, pre-existing ArtifactManager limitation"
#: on Border Crossing Breitenau.DE without its external
#: ``Border crossing control.vap``. A deliberately CONSERVATIVE allowlist --
#: not "copy every non-.inpx file" (that live-check script's own narrower,
#: script-scoped workaround) -- chosen to exclude documentation (.pdf) and
#: anything Vissim does not need to load/render the network:
#:   .layx                    -- saved layout(s). Vissim auto-applies a
#:                                same-STEM layout file found alongside the
#:                                .inpx it opens (confirmed: 0 references to
#:                                ".layx" anywhere in a live .inpx's own XML
#:                                -- this association is purely a directory/
#:                                filename convention, never an embedded
#:                                reference, which is also why a copied
#:                                .layx must keep its exact original name).
#:   .vap                      -- external VisVAP signal-controller programs
#:                                (referenced by exact relative filename from
#:                                a <signalController>'s program attribute).
#:   .pua                      -- parking-lot control files (same exact-
#:                                filename reference pattern as .vap).
#:   .prbc                     -- external PT-priority signal-controller
#:                                configs (JSON; confirmed referenced by
#:                                .inpx on BRT Priority Texas.US).
#:   .v3d                      -- 3D scenery/vehicle models (3D visualization).
#:   .jpg/.jpeg/.png/.bmp/.ecw -- background images an .inpx can reference by
#:                                relative path (confirmed: Border crossing
#:                                .inpx references "#data#Gesamt.jpg", where
#:                                ``#data#`` is Vissim's "this .inpx's own
#:                                directory" token).
#: Never a directory (results dirs, texture subfolders, per-network asset
#: folders) -- only direct FILE children of source_inpx.parent ever qualify,
#: so this can never recurse into (or accidentally sweep up) a results dir.
SIBLING_ASSET_EXTENSIONS = frozenset(
    {".layx", ".vap", ".pua", ".prbc", ".v3d", ".jpg", ".jpeg", ".png", ".bmp", ".ecw"}
)


def _copy_sibling_assets(source_inpx: Path, work_dir: Path) -> list[str]:
    """Copy ``source_inpx``'s sibling assets (:data:`SIBLING_ASSET_EXTENSIONS`)
    into ``work_dir``, preserving each file's exact original name.

    docs/phase2/DECISIONS.md's v2 unit ⑤ follow-up: ``ArtifactManager.
    clone()`` used to ``shutil.copyfile`` ONLY the .inpx, so any network
    whose signal controllers or layout depend on a sibling file (an external
    .vap, a same-stem .layx, ...) silently failed to init once loaded from
    the work dir. Names are never changed here -- an .inpx's own XML
    references some of these (.vap/.pua/.prbc) by exact relative filename,
    and a .layx's Vissim auto-load convention is itself a same-directory-
    same-stem match, so renaming either would break the very thing this is
    fixing.

    Best-effort per file: one sibling that cannot be read (permission error,
    vanishes between listing and copy, e.g. a multi-GB .ecw background) is
    skipped with a logged warning rather than failing the whole ``clone()``
    -- a caller should still get a working .inpx even if one background
    image cannot be copied.

    Returns the names actually copied (used by tests / the download
    endpoint's own duplicated allowlist check); never raises.
    """
    copied: list[str] = []
    try:
        entries = list(source_inpx.parent.iterdir())
    except OSError:
        logger.warning(
            "ArtifactManager.clone(): cannot list %s for sibling assets", source_inpx.parent
        )
        return copied

    for path in entries:
        try:
            if not path.is_file() or path.name == source_inpx.name:
                continue
            if path.suffix.lower() not in SIBLING_ASSET_EXTENSIONS:
                continue
            shutil.copyfile(path, work_dir / path.name)
            copied.append(path.name)
        except OSError:
            logger.warning(
                "ArtifactManager.clone(): could not copy sibling asset %s", path, exc_info=True
            )
    return copied


# ---------------------------------------------------------------------------
# Scenario-management normalization -- WORKING COPY ONLY, never source_inpx.
#
# ROOT CAUSE (verified live, backend/tools/probe_murdoch_load.py, 2026-07-31):
# the real client dataset (a PTV Vision "Place Under Scenario Management"
# export -- it sits next to project.vissimpdb/Scenarios/Modifications) makes
# VissimController.load_network's LoadNet call RAISE a generic, low-
# information com_error ("VISSIM.Vissim.2600: The network file could not be
# loaded.") for exactly one reason: the file's own
# ``<netPara underScenMngm="true">`` self-declaration. This is NOT a Vissim-
# version-compatibility gate (an older-installed-Vissim hypothesis was
# directly ruled out live: the installed build is NEWER than the failing
# file's own build, yet files from both an OLDER and a STILL NEWER build
# than the failing one load fine, and a header-swap test that rewrote ONLY
# the vissimVersion string to a known-good build, no other byte changed,
# still failed identically). Every Vissim 2026 example network checked
# declares ``underScenMngm="false"`` and loads fine; flipping ONLY that one
# attribute to "false" on a byte-identical-otherwise copy of the real
# client file was verified live to LOAD SUCCESSFULLY with object counts
# matching the source XML's own ground truth EXACTLY (312 links / 6 signal
# controllers / 51 vehicle inputs / 6 nodes / 216 conflict areas / 28
# vehicle types, including vehicle type 1030's real name "Transperth" read
# back correctly).
#
# This mirrors a fix this codebase already needed once before, for a
# DIFFERENT cloning path: services/simulation_network_builder.py's
# ``_make_network_standalone`` (written for the job-based "interactive"
# simulation run) already does
# ``net_para.set("underScenMngm", "false")`` for exactly this reason. The
# agent-edit/load_check cloning path (this module) never went through that
# builder, which is why it alone stayed broken against the real dataset.
#
# Deliberately NOT a ``LoadNet(path, additive=True)`` fix in
# services/vissim_com.py (tried and rejected): "additive" mode merges the
# file into Vissim's own "New" default in-memory project instead of
# replacing it, and was found live to silently ADD 11 extra vehicle types
# (39 live vs the file's own 28) that do not belong to the source network
# at all -- exactly the kind of silent-wrong-numbers pollution
# 00-EXECUTION-GUIDE.md SS4 warns about, and it would have had to run on
# whatever raw path a caller passes ``VissimController.load_network``,
# including the one call site (``services/vissim_tool.py``'s
# ``get_vissim_status``) that loads the ACTIVE SOURCE DIRECTLY, not a
# clone -- writing anywhere near that path is exactly what rule 4 forbids.
# Normalizing the WORKING COPY here, where this module already guarantees
# it owns a throwaway file, avoids both problems at once.
# ---------------------------------------------------------------------------

_UNDER_SCEN_MNGM_TRUE_RE = re.compile(r'\bunderScenMngm="true"')


def _disable_scenario_management_flag(working_path: Path) -> bool:
    """Flip ``<netPara underScenMngm="true">`` to ``"false"`` IN THE WORKING
    COPY (``working_path`` — never ``source_inpx``) if present, a plain
    regex scan/rewrite of the raw file (not a full XML parse/reserialize,
    so every other byte is untouched). See the module-level comment above
    for the full, empirically verified root-cause story.

    A false negative (attribute absent, or an unreadable file) is the safe
    default: the working copy is simply left as-is, exactly as it always
    was before this fix. Returns ``True`` iff a rewrite happened
    (informational only — no caller needs to branch on this)."""
    try:
        text = working_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    new_text, n = _UNDER_SCEN_MNGM_TRUE_RE.subn('underScenMngm="false"', text)
    if n == 0:
        return False
    try:
        working_path.write_text(new_text, encoding="utf-8")
    except OSError:
        logger.warning(
            "ArtifactManager: could not normalize underScenMngm in working copy %s",
            working_path, exc_info=True,
        )
        return False
    logger.info(
        "ArtifactManager: normalized underScenMngm=\"true\" -> \"false\" in the working "
        "copy %s (%d occurrence(s)) -- source_inpx untouched", working_path, n,
    )
    return True


@dataclass
class StepRecord:
    """One summarized step in an agent session's history.

    Fields match docs/phase2/02-ROADMAP.md verbatim: thought, action, params,
    observation, kpi. ``kpi`` is optional and ``None`` when the step produced
    no KPI reading (e.g. a read-only inspection step).
    """

    thought: str
    action: str
    params: dict[str, Any]
    observation: Any
    kpi: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought": self.thought,
            "action": self.action,
            "params": self.params,
            "observation": self.observation,
            "kpi": self.kpi,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepRecord":
        return cls(
            thought=data["thought"],
            action=data["action"],
            params=data["params"],
            observation=data["observation"],
            kpi=data.get("kpi"),
        )


@dataclass
class AgentState:
    """The agent session's persisted state: ⟨goal, artifacts, history, context⟩.

    - goal: the user's request, normalized.
    - artifacts: paths/metadata for the versioned .inpx work copy, the shadow
      model, and result .db paths after runs.
    - history: ordered list of ``StepRecord`` (summarized; trace.py holds the
      full on-disk event log).
    - context: dataset id, scenario id, COM session status, pending-approval
      flag, cached latest KPIs, accumulated constraint warnings.
    """

    goal: str
    artifacts: dict[str, Any]
    history: list[StepRecord]
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "artifacts": self.artifacts,
            "history": [record.to_dict() for record in self.history],
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        return cls(
            goal=data["goal"],
            artifacts=data["artifacts"],
            history=[StepRecord.from_dict(item) for item in data["history"]],
            context=data["context"],
        )

    def to_json(self) -> str:
        # ensure_ascii=False: this app is Korean-primary, and goal/history
        # text routinely carries non-ASCII content that must round-trip
        # literally rather than as \uXXXX escapes.
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> "AgentState":
        return cls.from_dict(json.loads(payload))


class ArtifactManager:
    """Owns the session work dir's versioned .inpx artifacts and shadow model.

    Usage: construct with the active dataset's source .inpx and a session
    work dir, call ``clone()`` once to create the mutable working copy, then
    ``save_version()`` after each edit to snapshot it, and ``rollback_to()``
    to restore a prior snapshot. ``shadow_model`` always reflects the most
    recently parsed state of ``working_path``.

    Version numbers never get reused or rewritten: even after a rollback,
    the next ``save_version()`` call advances past every version number ever
    seen in ``work_dir`` (including one written by an earlier process for the
    same session), so ``edit_NNN.inpx`` files remain a complete audit trail.
    """

    def __init__(self, source_inpx: Path | str, work_dir: Path | str) -> None:
        self.source_inpx = Path(source_inpx)
        self.work_dir = Path(work_dir)
        self.working_path = self.work_dir / "working.inpx"
        self._shadow_model: dict[str, Any] | None = None
        self._version = self._max_existing_version()
        # Cache-busting guard for build_shadow_model's mtime-keyed lru_cache
        # (tools_network.py) -- see _refresh_shadow_model.
        self._last_parsed_mtime: float | None = None

    def _max_existing_version(self) -> int:
        if not self.work_dir.exists():
            return 0
        numbers = [
            int(match.group(1))
            for path in self.work_dir.iterdir()
            if (match := _VERSION_RE.match(path.name))
        ]
        return max(numbers, default=0)

    def _version_path(self, version: int) -> Path:
        return self.work_dir / f"edit_{version:03d}.inpx"

    @property
    def version(self) -> int:
        """The most recently saved version number (0 if none saved yet).

        This is the "last good version" a caller reads before attempting a
        write, to know what to ``rollback_to()`` if postwrite verification
        fails (``rollback_to(0)`` restores the pristine clone — see
        ``rollback_to``).

        Snapshotted once at ``__init__`` (from a ``work_dir`` scan) and
        otherwise only ever advanced by THIS instance's own
        ``save_version()``/``rollback_to()`` calls — it does NOT
        automatically notice a version another ``ArtifactManager`` instance
        for the SAME ``work_dir`` has since saved. See :meth:`refresh_version`
        for a caller (e.g. a resumed HTTP turn) that needs an up-to-date
        read.
        """
        return self._version

    def refresh_version(self) -> int:
        """Re-scan ``work_dir`` and update :attr:`version` to the latest
        ``edit_NNN.inpx`` actually on disk, returning the new value.

        docs/phase2/DECISIONS.md's Bug 1 entry: a caller across separate HTTP
        requests (``routers/assistant.py``) constructs a FRESH
        ``ArtifactManager`` per request, synchronously, in the request
        handler — before any per-session serialization lock is acquired. If
        an EARLIER turn for the same session is still mid-write on another
        thread at that moment, the new instance's ``__init__``-time version
        scan can capture a version that is about to become stale the instant
        the earlier turn's own ``save_version()`` lands. Calling this
        (``routers/assistant.py``'s ``_ensure_working_copy`` does, right
        after the per-session lock is acquired — i.e. once no turn can still
        be concurrently writing) guarantees ``rollback_to(self.version)``
        targets the TRUE latest saved version instead of whatever was on
        disk at construction time.
        """
        self._version = self._max_existing_version()
        return self._version

    def _refresh_shadow_model(self) -> None:
        """Reparse ``working_path`` via ``build_shadow_model``, guaranteeing a
        FRESH (non-stale-cached) parse even when this write landed at the same
        filesystem-mtime tick as this instance's previous parse.

        ``build_shadow_model`` is ``lru_cache``-keyed on ``(path, mtime)``
        (tools_network.py); empirically, back-to-back writes to the same path
        can share one mtime tick on this filesystem, which would otherwise
        silently return the STALE cached parse from before this write. When
        that would happen, this nudges ``working_path``'s mtime strictly past
        the last mtime this instance parsed at — mtime is explicitly a
        "cache-busting arg" by that module's own docstring, so advancing it
        is the sanctioned way to force a fresh cache entry.
        """
        disk_mtime = self.working_path.stat().st_mtime
        next_mtime = disk_mtime
        if self._last_parsed_mtime is not None and next_mtime <= self._last_parsed_mtime:
            next_mtime = self._last_parsed_mtime + 0.001
            os.utime(self.working_path, (next_mtime, next_mtime))
        self._shadow_model = build_shadow_model(self.working_path)
        self._last_parsed_mtime = next_mtime

    def _guard_against_clobbering_unsaved_work(self) -> None:
        """Refuse to silently discard an existing working copy that carries
        edits no saved version reflects.

        ``clone()`` overwrites ``working_path`` unconditionally once past
        this guard. Called a second time over the same session work dir —
        e.g. a second ``ArtifactManager`` instance constructed over it; there
        is no cross-process lock, per docs/phase2/DECISIONS.md's "version
        counter is single-instance by design" note — it would otherwise
        silently discard any mutation made since the last ``save_version()``,
        or, if ``save_version()`` was never called at all, discard the ENTIRE
        working copy with no snapshot to fall back on.

        Resuming a session whose working copy is still byte-identical to its
        latest saved version (the common "new process, same session" case)
        is safe and explicitly allowed — this is NOT gated on "has a working
        copy ever been saved", only on "would this clone silently lose
        something".

        Raises:
            RuntimeError: the working copy exists and either no saved
                version exists yet, or it has diverged from the latest one.
        """
        if not self.working_path.exists():
            return
        if self._version == 0:
            raise RuntimeError(
                f"Refusing to clone into {self.work_dir}: a working copy already exists at "
                f"{self.working_path!s} with no saved version to fall back on. Calling "
                "clone() again would silently discard it. Use the existing ArtifactManager "
                "instance, or call save_version() first if these edits should be kept."
            )
        latest_version_path = self._version_path(self._version)
        if self.working_path.read_bytes() != latest_version_path.read_bytes():
            raise RuntimeError(
                f"Refusing to clone into {self.work_dir}: the working copy at "
                f"{self.working_path!s} has diverged from its latest saved version "
                f"({latest_version_path.name}) — cloning again would silently discard those "
                "edits. Call save_version() first if they should be kept, or rollback_to() "
                "an explicit version first if they should be discarded."
            )

    def clone(self) -> dict[str, Any]:
        """Copy the source .inpx into the work dir as the mutable working copy.

        Never touches ``source_inpx`` — it is copied, never moved or opened
        for writing. Also copies ``source_inpx``'s sibling assets (external
        signal-controller programs, layout files, background images -- see
        :data:`SIBLING_ASSET_EXTENSIONS`) alongside it, under their original
        names, so a network that depends on them (docs/phase2/DECISIONS.md's
        v2 unit ⑤ follow-up) still inits correctly from the work dir. Parses
        the initial shadow model and returns a deep copy of it (see the
        ``shadow_model`` property).

        Also normalizes a scenario-management export's
        ``underScenMngm="true"`` self-declaration to ``"false"`` in the
        WORKING COPY only (:func:`_disable_scenario_management_flag` — see
        its module-level comment for why: without this, ``VissimController.
        load_network`` cannot ``LoadNet`` the working copy at all for a
        dataset placed under PTV Vision scenario management).

        Raises:
            RuntimeError: see ``_guard_against_clobbering_unsaved_work`` —
                refuses to overwrite a working copy that carries edits no
                saved version reflects.
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._guard_against_clobbering_unsaved_work()
        shutil.copyfile(self.source_inpx, self.working_path)
        _copy_sibling_assets(self.source_inpx, self.work_dir)
        _disable_scenario_management_flag(self.working_path)
        self._refresh_shadow_model()
        return self.shadow_model

    def save_version(self) -> int:
        """Snapshot the current working copy to the next ``edit_NNN.inpx``.

        Reparses the shadow model from the working copy after saving.
        Returns the new version number.

        Raises:
            RuntimeError: if ``clone()`` has not been called yet.
        """
        if not self.working_path.exists():
            raise RuntimeError("ArtifactManager.clone() must be called before save_version()")

        self._version += 1
        shutil.copyfile(self.working_path, self._version_path(self._version))
        self._refresh_shadow_model()
        return self._version

    def rollback_to(self, version: int) -> None:
        """Restore the working copy byte-identically from a saved version.

        Refreshes the shadow model from the restored file. Does not touch
        the version counter, so the next ``save_version()`` advances past
        every version ever written, never overwriting history.

        ``version=0`` is special-cased to mean "the pristine clone": it
        restores straight from ``source_inpx`` rather than looking for a
        numbered snapshot, because ``clone()`` does not itself create an
        ``edit_000.inpx`` (numbering starts at 1, from the first
        ``save_version()`` call). This lets a caller roll back a session's
        very first write — before any ``save_version()`` has ever run — back
        to the untouched clone, not just to a later saved version. Re-applies
        the same scenario-management normalization ``clone()`` does
        (:func:`_disable_scenario_management_flag`) so this path can't
        silently regress a working copy back to an unloadable state.

        Raises:
            RuntimeError: ``version=0`` but ``clone()`` has not been called
                yet (no working copy to restore into).
            FileNotFoundError: ``version`` is not 0 and has no saved snapshot.
        """
        if version == 0:
            if not self.working_path.exists():
                raise RuntimeError(
                    "ArtifactManager.clone() must be called before rollback_to(0)"
                )
            shutil.copyfile(self.source_inpx, self.working_path)
            # Mirrors clone()'s own normalization -- restoring straight from
            # source_inpx must not silently re-introduce underScenMngm="true"
            # into the working copy (see _disable_scenario_management_flag's
            # module-level comment).
            _disable_scenario_management_flag(self.working_path)
            self._refresh_shadow_model()
            return

        version_path = self._version_path(version)
        if not version_path.exists():
            raise FileNotFoundError(
                f"No saved artifact version {version!r} at {version_path}"
            )
        shutil.copyfile(version_path, self.working_path)
        self._refresh_shadow_model()

    @property
    def shadow_model(self) -> dict[str, Any] | None:
        """A deep copy of the most recently parsed shadow model.

        ``build_shadow_model`` (tools_network.py) is ``lru_cache``-backed, so
        the dict it returns is SHARED across every caller hitting the same
        (path, mtime) key — other ``ArtifactManager`` instances and any
        ``tools_network`` read tool pointed at the same file included.
        Returning that shared reference directly would let one caller's
        in-place mutation corrupt every other caller's view. Every access
        returns an independent ``copy.deepcopy`` instead; ``None`` (before
        ``clone()`` has ever run) passes through unchanged.
        """
        if self._shadow_model is None:
            return None
        return copy.deepcopy(self._shadow_model)

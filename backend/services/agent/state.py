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
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.agent.tools_network import build_shadow_model

_VERSION_RE = re.compile(r"^edit_(\d{3})\.inpx$")


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
        """
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
        for writing. Parses the initial shadow model and returns a deep copy
        of it (see the ``shadow_model`` property).

        Raises:
            RuntimeError: see ``_guard_against_clobbering_unsaved_work`` —
                refuses to overwrite a working copy that carries edits no
                saved version reflects.
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._guard_against_clobbering_unsaved_work()
        shutil.copyfile(self.source_inpx, self.working_path)
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
        to the untouched clone, not just to a later saved version.

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

"""In-memory agent session state, JSON persistence, and versioned .inpx artifacts.

``AgentState`` is the ⟨goal, artifacts, history, context⟩ tuple described in
docs/phase2/01-ARCHITECTURE.md §2. It holds the *summarized* in-memory history
of an agent session as ``StepRecord`` entries; the full step-by-step event log
is trace.py's job (``backend/services/agent/trace.py``) and is not duplicated
here.

``ArtifactManager`` owns the on-disk lifecycle of the session's working copy
of the active dataset's .inpx file: it clones the source once, then snapshots
every save as an immutable, monotonically-numbered ``edit_NNN.inpx`` version,
reparsing the shadow model (via ``services.inpx_parser.parse_network``) after
every save and rollback. The user's source dataset is read-only input and is
never written to.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.inpx_parser import parse_network

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

    def clone(self) -> dict[str, Any]:
        """Copy the source .inpx into the work dir as the mutable working copy.

        Never touches ``source_inpx`` — it is copied, never moved or opened
        for writing. Parses the initial shadow model and returns it.
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source_inpx, self.working_path)
        self._shadow_model = parse_network(str(self.working_path))
        return self._shadow_model

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
        self._shadow_model = parse_network(str(self.working_path))
        return self._version

    def rollback_to(self, version: int) -> None:
        """Restore the working copy byte-identically from a saved version.

        Refreshes the shadow model from the restored file. Does not touch
        the version counter, so the next ``save_version()`` advances past
        every version ever written, never overwriting history.

        Raises:
            FileNotFoundError: if ``version`` has no saved snapshot.
        """
        version_path = self._version_path(version)
        if not version_path.exists():
            raise FileNotFoundError(
                f"No saved artifact version {version!r} at {version_path}"
            )
        shutil.copyfile(version_path, self.working_path)
        self._shadow_model = parse_network(str(self.working_path))

    @property
    def shadow_model(self) -> dict[str, Any] | None:
        """The most recently parsed shadow model (``inpx_parser.parse_network`` output)."""
        return self._shadow_model

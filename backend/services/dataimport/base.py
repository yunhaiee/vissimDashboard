"""The adapter contract: what every regional data adapter must implement.

Roadmap 0.7 / TK requirement R1.3. One sentence version: **an adapter turns
its region's files into the neutral records of ``schema.py`` and a list of
:class:`ImportIssue`, and it does neither of those things by guessing.**

Three obligations, all enforceable and all tested:

* **MUST NOT invent.** A datum the source does not carry stays ``None``. If
  the absence matters, say so — ``NeutralRecord.absent`` for the machine, an
  :class:`ImportIssue` for the human. Filling a field with a plausible default
  is the exact failure mode this project exists to prevent, moved one layer
  upstream of Vissim.
* **MUST report, not raise.** Every *expected* data problem (a missing tab, a
  malformed row, a coverage shortfall, an unmappable class code) is an
  ``ImportIssue`` inside a returned :class:`ImportResult`. Exceptions are
  reserved for programming errors and genuinely unreadable inputs
  (:class:`AdapterError`). A parse that found nothing usable still *returns* —
  with ``REFUSE`` issues explaining why.
* **MUST preserve provenance.** Every record cites its file and row. This is
  what lets a report table, a constraint message or an approval card say where
  a number came from six months later.

``sniff()`` exists so a user can drop files in without telling us what they
are: each registered adapter scores its confidence, and
:func:`select_adapter` picks the best one above a threshold. Sniffing must be
cheap, read-only, and must never raise on a file it does not recognise —
"I don't know this" is confidence ``0.0``, not an exception.

Non-COM by construction: nothing here imports Vissim.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .schema import (
    DetectorCountData,
    Provenance,
    SignalControllerData,
    SiteMetadata,
    TurningCountData,
)


class AdapterError(Exception):
    """An input we cannot process *at all* — not a data-quality finding.

    Reserved for: a file that does not exist, an unreadable/undecodable byte
    stream, or an internal bug. Everything a data reviewer could act on is an
    :class:`ImportIssue` instead. If you are tempted to raise this because a
    column was missing, you want a ``REFUSE`` issue.
    """


class IssueSeverity(str, Enum):
    """Severity vocabulary, aligned with the constraint engine's tiers.

    ``services/agent/constraints.py`` uses ``"refuse" | "warn"``; the import
    layer adds ``"info"`` because an import legitimately reports things that
    carry no judgement at all (what was parsed, what was absent and why).

    * ``REFUSE`` — the data is internally contradictory, or it fails an
      explicit numeric minimum stated by the governing authority. Blocked by
      default; overridable only by a recorded human decision, the same way an
      edit refusal is.
    * ``WARN`` — the data is coherent and meets the minimums, but is
      unrepresentative, unusual, or missing a recommended property. It rides
      along on the result and belongs in the report.
    * ``INFO`` — an observation. Never blocks, never scolds.
    """

    REFUSE = "refuse"
    WARN = "warn"
    INFO = "info"


class ImportIssue(BaseModel):
    """One structured finding from parsing or from the quality gate.

    ``code`` is the stable machine identifier (snake_case, unique per check) —
    tests assert on codes, never on prose. ``message`` is developer/reviewer
    English; ``messages`` is the i18n hook for the ko-primary UI layer (the
    same ``{lang: text}`` shape ``constraints.Finding`` uses), populated when a
    finding reaches the dashboard.

    ``citation`` names the authority a rule comes from ("MRWA Appendix-A p.11",
    "MRWA op p.59") so a refusal can defend itself. Rules with no written
    authority leave it ``None`` rather than borrowing one.
    """

    model_config = ConfigDict(extra="forbid")

    severity: IssueSeverity
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    messages: dict[str, str] = Field(
        default_factory=dict, description="Localised messages keyed by language code."
    )
    subject: str | None = Field(
        default=None, description='What the finding is about: "site 359 / approach N".'
    )
    provenance: Provenance | None = Field(
        default=None,
        description="Set whenever the finding is attributable to a specific input record.",
    )
    citation: str | None = None
    context: dict[str, Any] = Field(
        default_factory=dict, description="Numeric evidence behind the finding."
    )


class AdapterInfo(BaseModel):
    """Adapter identity. Goes into the report so a model can say which
    adapter, at which version, read its inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, description='Stable id, e.g. "scats_wa".')
    region: str = Field(min_length=1, description='e.g. "AU-WA", "KR-Daejeon".')
    source_system: str = Field(min_length=1, description='e.g. "SCATS", "스마트교차로".')
    version: str = Field(min_length=1, description="Adapter version, bumped when mapping changes.")
    description: str | None = None


class SniffResult(BaseModel):
    """An adapter's self-assessment of a file set.

    ``confidence`` is 0.0–1.0 and comparable across adapters, so keep it
    honest: 1.0 means "these are unmistakably my files", ~0.5 means "the names
    match but I have not seen the contents", 0.0 means "not mine". ``reason``
    is always populated — a bare number tells a user nothing when the wrong
    adapter wins.
    """

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    matched_files: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    """Everything one parse produced: neutral records + structured findings.

    A parse *always* returns one of these. ``ok`` is false when any ``REFUSE``
    issue is present, which is the signal the data-quality gate (R2.5) and the
    import UI branch on.
    """

    model_config = ConfigDict(extra="forbid")

    adapter: AdapterInfo
    sites: list[SiteMetadata] = Field(default_factory=list)
    controllers: list[SignalControllerData] = Field(default_factory=list)
    counts: list[TurningCountData] = Field(default_factory=list)
    detector_counts: list[DetectorCountData] = Field(default_factory=list)
    issues: list[ImportIssue] = Field(default_factory=list)
    source_files: list[str] = Field(
        default_factory=list, description="Inputs actually consumed, as named in provenance."
    )

    @property
    def refusals(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.REFUSE]

    @property
    def warnings(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.WARN]

    @property
    def infos(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.INFO]

    @property
    def ok(self) -> bool:
        """True when nothing refuses. Warnings never block."""
        return not self.refusals

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [
            issue.code
            for issue in self.issues
            if severity is None or issue.severity is severity
        ]

    def add_issues(self, issues: Iterable[ImportIssue]) -> "ImportResult":
        self.issues.extend(issues)
        return self


class DataAdapter(ABC):
    """Base class every regional adapter implements.

    Subclasses declare :attr:`info` as a class attribute and implement
    :meth:`sniff` and :meth:`parse`. The contract obligations in the module
    docstring are the *behavioural* half; :func:`check_adapter_conformance`
    checks the structural half so a new adapter's first test is one line.
    """

    #: Set by every concrete adapter.
    info: AdapterInfo

    @abstractmethod
    def sniff(self, files: Sequence[Path]) -> SniffResult:
        """Score how likely this file set is one of ours. Never raises."""

    @abstractmethod
    def parse(self, files: Sequence[Path]) -> ImportResult:
        """Turn the files into neutral records + issues.

        Returns even when the data is unusable — with ``REFUSE`` issues saying
        why. Raises :class:`AdapterError` only for unreadable inputs or bugs.
        """

    # -- helpers every adapter may use -------------------------------------

    def issue(
        self,
        severity: IssueSeverity,
        code: str,
        message: str,
        **kwargs: Any,
    ) -> ImportIssue:
        return ImportIssue(severity=severity, code=code, message=message, **kwargs)

    def empty_result(self) -> ImportResult:
        return ImportResult(adapter=self.info)


def check_adapter_conformance(adapter: Any) -> list[str]:
    """Structural conformance check — returns a list of problems (empty = ok).

    Checks what can be checked without running a parse: it is a real
    :class:`DataAdapter`, it declares a valid :class:`AdapterInfo`, and both
    protocol methods are actually overridden. The behavioural obligations
    (never invent, report don't raise, preserve provenance) are proven by the
    adapter's own tests — see ``tests/test_dataimport_scats_wa.py``.
    """
    problems: list[str] = []
    if not isinstance(adapter, DataAdapter):
        problems.append(f"{type(adapter).__name__} is not a DataAdapter subclass instance")
        return problems

    info = getattr(adapter, "info", None)
    if not isinstance(info, AdapterInfo):
        problems.append("adapter.info must be an AdapterInfo instance")

    for method in ("sniff", "parse"):
        own = getattr(type(adapter), method, None)
        base_method = getattr(DataAdapter, method, None)
        if own is None or own is base_method:
            problems.append(f"{type(adapter).__name__} does not override {method}()")
    return problems


# ---------------------------------------------------------------------------
# Registry
#
# Deliberately a plain module-level dict rather than import-time magic: a
# region's adapter is registered explicitly, so "which adapters exist" is
# greppable and a half-finished adapter cannot silently start winning sniffs.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, DataAdapter] = {}

#: Below this, ``select_adapter`` refuses to guess. Chosen conservatively:
#: picking the wrong adapter produces confidently mis-mapped fields, which is
#: worse than telling the user we do not recognise their files.
SNIFF_CONFIDENCE_THRESHOLD = 0.5


def register_adapter(adapter: DataAdapter, *, replace: bool = False) -> DataAdapter:
    problems = check_adapter_conformance(adapter)
    if problems:
        raise AdapterError(f"adapter does not conform: {'; '.join(problems)}")
    name = adapter.info.name
    if name in _REGISTRY and not replace:
        raise AdapterError(f"adapter {name!r} is already registered")
    _REGISTRY[name] = adapter
    return adapter


def unregister_adapter(name: str) -> None:
    _REGISTRY.pop(name, None)


def list_adapters() -> list[DataAdapter]:
    return list(_REGISTRY.values())


def get_adapter(name: str) -> DataAdapter | None:
    return _REGISTRY.get(name)


def select_adapter(
    files: Sequence[Path], *, threshold: float = SNIFF_CONFIDENCE_THRESHOLD
) -> tuple[DataAdapter | None, list[tuple[str, SniffResult]]]:
    """Ask every registered adapter to sniff; return the winner + all scores.

    Returns ``(None, scores)`` when nothing clears ``threshold`` — the caller
    then tells the user which formats we *do* recognise instead of picking the
    least-bad option. An adapter whose ``sniff`` raises is scored 0.0 and its
    failure recorded in the reason, so one broken adapter cannot break intake.
    """
    scores: list[tuple[str, SniffResult]] = []
    for adapter in _REGISTRY.values():
        try:
            result = adapter.sniff(files)
        except Exception as exc:  # a sniff must never raise; treat as "not mine"
            result = SniffResult(confidence=0.0, reason=f"sniff raised {type(exc).__name__}: {exc}")
        scores.append((adapter.info.name, result))

    scores.sort(key=lambda pair: pair[1].confidence, reverse=True)
    if scores and scores[0][1].confidence >= threshold:
        return _REGISTRY[scores[0][0]], scores
    return None, scores


__all__ = [
    "AdapterError",
    "AdapterInfo",
    "DataAdapter",
    "ImportIssue",
    "ImportResult",
    "IssueSeverity",
    "SNIFF_CONFIDENCE_THRESHOLD",
    "SniffResult",
    "check_adapter_conformance",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "select_adapter",
    "unregister_adapter",
]

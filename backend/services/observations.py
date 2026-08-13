"""Per-workspace observation store — persisted field counts (Phase 2 unit U10).

Roadmap row 2.1's last named dependency. Before this module the reality check's
observed side was exactly ONE file per workspace: ``real_counts.xlsx``, loaded
by ``services.validation.load_base_workbook``. The Daejeon drop is eleven
workbooks × five weeks of 15-minute counts, read by the 1.2 adapter into
neutral :class:`~services.dataimport.schema.TurningCountData` records — and
nothing persisted any of it. U10 closes that: ingest through the adapter + the
quality gate, keep the AGGREGATES, and let ``run_reality_check`` use a stored
observation SET as its observed side.

What is stored, and what is deliberately not
--------------------------------------------
**Aggregates, never records.** A set holds, per declared period and per site:
the node total, the per-movement volumes, the interval count and the span —
the shape ``services.agent.reality_check`` consumes. The 3.09M neutral records
a full drop parses into are dropped as soon as they have been added up.

That is not a size preference, it is the MEASURED hazard (chip
``task_b4b38b63``): parsing the whole drop in one ``adapter.parse(files)``
call peaks at ~6.3 GB, because every record is a retained pydantic model and
they are all alive at once inside one :class:`ImportResult`. So
:func:`ingest_observation_set` parses **one workbook at a time**, gates it,
adds its counts into the accumulator, and releases the result before opening
the next file. Peak memory is therefore one workbook's records plus the
accumulator, not the drop's.

The residual is disclosed rather than hidden: the adapter's contract is
``parse(files) -> ImportResult``, so one WORKBOOK is the smallest unit this
layer can ask for. A record-streaming adapter API (``iter_counts``) would take
the peak to a constant; it does not exist today and inventing one inside
``dataimport`` is not this unit's change. See ``docs/phase2/DECISIONS.md``
protocol if that gap is ever closed.

Periods are declared, never inferred
------------------------------------
A five-week survey compared in one lump against a one-hour run would
manufacture a demand error of the same shape ``reality_check``'s
``MULTI_SEED_VOLUMES_NOT_COMPARABLE`` refuses. Which window represents the
modelled period is the modeller's decision — exactly the doctrine
``dataimport.demand.build_demand_seeding(window=…)`` already states — so
ingest REQUIRES at least one named period and records it, and the window
predicate is the same one demand seeding uses (``start <= interval_start <
end``).

Refusals block storage
----------------------
A REFUSE-tier finding from the adapter or the gate means the set is **not
stored at all** and the gate report is returned instead. WARN/INFO ride along
in the stored summary — including 1.2's client finding that every real
workbook's final 23:45 bin is missing (``daejeon_declared_window_not_covered``),
which must survive into the stored set rather than being flattened away.

Storage layout and crash-safety
-------------------------------
``<workspace>/observations/registry.json`` — one registry per workspace, so
cross-workspace invisibility is physical, not a filter. The read/write cycle
is unit U6's (``services/simulation_runs.py``): a module ``RLock`` held across
read → mutate → write, a same-directory temp file + fsync + ``os.replace``, and
a registry that does not parse is QUARANTINED aside with its bytes intact
rather than silently replaced. Cross-PROCESS locking is out of scope here for
the same reason it is there: Prism is one uvicorn process, and the real answer
is roadmap P③'s SQLite migration, not an advisory lock file.

No COM, no Vissim, no network. Everything here runs against synthetic
workbooks on a laptop.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services import workspaces
from services.dataimport import (
    AdapterError,
    DataAdapter,
    ImportIssue,
    ImportResult,
    IssueSeverity,
    QualityThresholds,
    apply_quality_gate,
    get_adapter,
    list_adapters,
    select_adapter,
)
from services.dataimport.quality import is_all_class_total
from services.dataimport.schema import DetectorCountData, TurningCountData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

OBSERVATIONS_DIRNAME = "observations"
REGISTRY_FILENAME = "registry.json"
STAGING_DIRNAME = "staging"

#: Same hex-only, length-capped shape ``services.workspaces`` and
#: ``routers/network.py`` use for every client-supplied id in this repo: a
#: directory-name shape that cannot itself hold a separator or a ".." segment.
_SET_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

#: Workbook suffixes the import layer can open at all.
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")

#: A staged upload older than this is swept on the next stage call. Same
#: rationale as ``workbook_mapping.STAGING_TTL_SECONDS``: staging is a
#: multi-step UI's scratch space, not storage.
STAGING_TTL_SECONDS = 6 * 60 * 60

#: Bounded everywhere (this repo has a scar about unbounded list responses).
MAX_FILES_PER_SET = 64
MAX_PERIODS_PER_SET = 24
MAX_STORED_ISSUES = 200
MAX_SET_NAME_CHARS = 120

#: How many quarantine incidents a registry keeps; newest last.
_MAX_INCIDENTS = 20

#: Serializes read-modify-write of a registry FILE inside this process.
#: Re-entrant for the same reason U6's is: the quarantine path writes a fresh
#: registry from inside a read.
_REGISTRY_LOCK = threading.RLock()

_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 0.02

#: ``str(registry path)`` -> disclosure / last good record count. Keyed by path
#: so each workspace (and each test tmp root) gets its own state, exactly as
#: ``simulation_runs`` keys its two dicts.
_registry_health: dict[str, dict[str, Any]] = {}
_last_known_record_count: dict[str, int] = {}

#: The movement key the store aggregates on and the reality check joins on:
#: ``"<approach leg>|<turn>"``. It is a LABEL, not an identity — which Vissim
#: movement it is remains the caller's knowledge (``movement_ids``), the same
#: way the site->node join is. "|" because a Korean approach label ("북측방면")
#: will never contain one, so the two halves stay separable for a human reader.
MOVEMENT_ID_SEPARATOR = "|"

#: ``reality_check.observed_nodes`` sniffs a payload by looking for these keys
#: before deciding whether it is a loader payload or an already-aggregated
#: ``{node_id: NodeRealCounts}`` mapping. A node id spelled like one of them
#: would flip that branch and be read as a workbook payload, so the mapping
#: refuses them by name instead of producing a silently wrong observed side.
_RESERVED_NODE_IDS = frozenset({"counts", "warnings", "periods", "exists"})


# ---------------------------------------------------------------------------
# Errors + refusal codes
# ---------------------------------------------------------------------------


class ObservationStoreError(RuntimeError):
    """Base class for every failure this module raises."""


class RegistryUnavailableError(ObservationStoreError):
    """The registry exists but its current contents could not be read.

    Exists so a WRITE can refuse: overwriting bytes we could not read turns a
    transient I/O problem into permanent data loss. Read paths degrade to "no
    sets" and disclose through :func:`registry_health_status`; write paths
    propagate this.
    """


class ObservationRefusal(ObservationStoreError):
    """An ingest that must not be stored, with the reason as data.

    ``code`` is stable and machine-readable; ``messages`` is the ko/en/ja
    triple the dashboard renders; ``detail`` carries the evidence (the gate
    report, the offending names). The router turns this into a 4xx whose body
    IS ``to_dict()`` — a refusal with a reason, never a bare 400.
    """

    def __init__(
        self,
        code: str,
        messages: Mapping[str, str],
        *,
        status: int = 422,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {messages.get('en') or ''}")
        self.code = code
        self.messages = dict(messages)
        self.status = status
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "stored": False,
            "reason_code": self.code,
            "message": self.messages,
            **self.detail,
        }


class StagedWorkbookNotFoundError(ObservationStoreError):
    """An upload token is unknown, malformed, or has expired."""


#: The observed side could not be built from a stored set. These are the
#: store's OWN codes; ``services.agent.tools_metrics`` maps them onto
#: ``reality_check``'s reason vocabulary rather than either module importing
#: the other (this package must not depend on services.agent).
SET_NOT_FOUND = "SET_NOT_FOUND"
PERIOD_ABSENT = "PERIOD_ABSENT"
SITE_NODE_MAP_MISSING = "SITE_NODE_MAP_MISSING"
NO_MAPPED_SITES = "NO_MAPPED_SITES"


# ---------------------------------------------------------------------------
# Declared periods
# ---------------------------------------------------------------------------


class ObservationPeriod(BaseModel):
    """One named window of the survey the aggregates are cut into.

    Naive datetimes only. The Daejeon export states no timezone at all (the
    adapter marks ``SiteMetadata.timezone`` absent for exactly that reason), so
    its interval stamps are naive; accepting an aware bound here would either
    raise on the first comparison or silently shift the window by an offset
    nobody stated.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _window_is_naive_and_positive(self) -> "ObservationPeriod":
        for label, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is not None:
                raise ValueError(
                    f"period {self.name!r} {label} carries a timezone offset; the count export "
                    "states no timezone, so its interval stamps are naive and a window must be "
                    "too (comparing the two would shift the window by an offset nobody stated)"
                )
        if self.end <= self.start:
            raise ValueError(f"period {self.name!r} ends at or before it starts")
        return self


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def observations_dir() -> Path:
    """``<current workspace>/observations``. Derived per call from the bound
    workspace, like every other workspace-scoped path since unit 1.4 — so two
    workspaces cannot see each other's sets, physically rather than by filter."""
    return workspaces.workspace_dir(workspaces.current_workspace_id()) / OBSERVATIONS_DIRNAME


def registry_path() -> Path:
    return observations_dir() / REGISTRY_FILENAME


def staging_dir() -> Path:
    return observations_dir() / STAGING_DIRNAME


# ---------------------------------------------------------------------------
# Staging — hold uploaded bytes without python-multipart
# ---------------------------------------------------------------------------
#
# The house pattern for "upload a workbook, then act on it in a second call"
# is routers/validation.py's inspect -> token -> preview/import flow: a raw
# request body plus an ``X-Filename`` header, staged server-side under a token.
# It is used here for the same reason it is used there (this backend has no
# python-multipart dependency and requirements.txt is not this unit's to
# change), with one difference that matters: ``workbook_mapping.stage_workbook``
# rewrites the upload to ``source.xlsx``, and the Daejeon adapter reads the SITE
# ID and the declared collection window out of the FILE NAME (1.2's documented
# deviation). Renaming the file would destroy both, so this staging area keeps
# the original name inside a per-token directory.


def stage_workbook(filename: str, content: bytes) -> dict[str, Any]:
    """Stage one uploaded workbook and return ``{token, filename, bytes}``.

    Raises:
        ObservationRefusal: empty bytes, a non-workbook suffix, or a byte
            stream openpyxl cannot open as a workbook. Validating at STAGE time
            means a bad file is rejected while the user is still looking at the
            upload, not at ingest time next to ten good ones.
    """
    name = Path(str(filename or "")).name.strip()
    if not name:
        raise ObservationRefusal(
            "observation_upload_no_filename",
            {
                "ko": "파일 이름이 없습니다. 대전 카운트 내보내기는 파일 이름에 교차로명과 "
                "집계 기간이 들어 있으며, 어댑터가 그 값을 읽으므로 이름 없이는 받을 수 없습니다.",
                "en": "The upload carries no file name. The Daejeon export states the intersection "
                "name and the declared collection window IN the file name and the adapter reads "
                "both, so a nameless upload cannot be accepted.",
                "ja": "ファイル名がありません。大田のカウント出力は交差点名と集計期間をファイル名に"
                "含んでおり、アダプタがそれを読むため、名前なしでは受け付けられません。",
            },
            status=400,
        )
    if Path(name).suffix.lower() not in WORKBOOK_SUFFIXES:
        raise ObservationRefusal(
            "observation_upload_not_a_workbook",
            {
                "ko": f"'{name}'은(는) 엑셀 워크북이 아닙니다(허용: "
                f"{', '.join(WORKBOOK_SUFFIXES)}).",
                "en": f"{name!r} is not an Excel workbook (allowed: "
                f"{', '.join(WORKBOOK_SUFFIXES)}).",
                "ja": f"'{name}' はExcelワークブックではありません(許可: "
                f"{', '.join(WORKBOOK_SUFFIXES)})。",
            },
            status=400,
            detail={"filename": name},
        )
    if not content:
        raise ObservationRefusal(
            "observation_upload_empty",
            {
                "ko": f"'{name}'의 내용이 비어 있습니다.",
                "en": f"{name!r} is empty.",
                "ja": f"'{name}' の内容が空です。",
            },
            status=400,
            detail={"filename": name},
        )

    _purge_stale_staging()
    token = uuid4().hex
    target_dir = staging_dir() / token
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / name
    path.write_bytes(content)

    # Prove it opens BEFORE the token is handed out. Read-only + the adapter's
    # own opener, so a byte stream that only openpyxl's write path tolerates
    # cannot pass here and fail at ingest.
    try:
        from services.dataimport.adapters.daejeon import _open_worksheet

        _open_worksheet(path).close()
    except Exception as exc:  # noqa: BLE001 - any unreadable byte stream
        shutil.rmtree(target_dir, ignore_errors=True)
        raise ObservationRefusal(
            "observation_upload_unreadable",
            {
                "ko": f"'{name}'을(를) 엑셀 워크북으로 열 수 없습니다: {exc}",
                "en": f"{name!r} could not be opened as an Excel workbook: {exc}",
                "ja": f"'{name}' をExcelワークブックとして開けませんでした: {exc}",
            },
            status=400,
            detail={"filename": name},
        ) from exc

    return {"token": token, "filename": name, "bytes": len(content)}


def staged_workbook_path(token: str) -> Path:
    """The staged file for ``token``, validated twice (regex, then containment).

    Raises:
        StagedWorkbookNotFoundError: unknown, malformed, or expired.
    """
    if not token or not _TOKEN_RE.match(token):
        raise StagedWorkbookNotFoundError(
            "Unknown or expired upload token. Please re-upload the file."
        )
    root = staging_dir()
    target = (root / token).resolve()
    try:
        target.relative_to(root.resolve())
    except (ValueError, OSError) as exc:  # pragma: no cover - the regex rules this out
        raise StagedWorkbookNotFoundError("Unknown or expired upload token.") from exc
    if not target.is_dir():
        raise StagedWorkbookNotFoundError(
            "Unknown or expired upload token. Please re-upload the file."
        )
    files = sorted(item for item in target.iterdir() if item.is_file())
    if not files:
        raise StagedWorkbookNotFoundError(
            "Unknown or expired upload token. Please re-upload the file."
        )
    return files[0]


def discard_staged_workbook(token: str) -> None:
    root = staging_dir()
    if not token or not _TOKEN_RE.match(token):
        return
    target = root / token
    if target.is_dir() and target.parent == root:
        shutil.rmtree(target, ignore_errors=True)


def _purge_stale_staging(max_age_seconds: int = STAGING_TTL_SECONDS) -> None:
    root = staging_dir()
    if not root.is_dir():
        return
    now = time.time()
    for child in root.iterdir():
        try:
            if child.is_dir() and now - child.stat().st_mtime > max_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


# ---------------------------------------------------------------------------
# The aggregation
# ---------------------------------------------------------------------------


def movement_id(count: TurningCountData) -> str:
    """``"<approach leg>|<turn>"`` — the stored per-movement key.

    The turn's *geometric* value (``left``/``through``/``right``/``u_turn``) is
    used when the adapter mapped one, and the source's own token otherwise, so
    an unmapped movement is still named rather than merged into a bucket.
    """
    turn = count.movement.turn.value if count.movement.turn else (count.movement.turn_raw or "?")
    return f"{count.approach.leg_id}{MOVEMENT_ID_SEPARATOR}{turn}"


def is_summable(count: TurningCountData) -> bool:
    """Whether this record may enter a sum — the two "this row is already a sum
    of other rows" axes of the neutral model, and both of them.

    Identical in meaning to ``dataimport.demand.seedable_counts``, which is the
    LIST form of the same predicate pair; a test pins the two against each
    other so there is still exactly one definition of "summable". This module
    needs the per-record form because materialising a second filtered list of a
    workbook's counts would double the very peak U10 exists to bound.
    """
    return not count.movement.is_approach_total and not is_all_class_total(count)


class _Aggregator:
    """Streaming accumulator: counts in, aggregates out, records never kept.

    One instance per ingest. :meth:`add` is called once per WORKBOOK and holds
    no reference to the records it walked, so the caller can drop the
    :class:`ImportResult` immediately afterwards.
    """

    def __init__(self, periods: Sequence[ObservationPeriod]) -> None:
        self._periods = list(periods)
        # period name -> site id -> accumulator
        self._by_period: dict[str, dict[str, dict[str, Any]]] = {
            period.name: {} for period in self._periods
        }
        self._site_names: dict[str, str | None] = {}
        self.records_seen = 0
        self.records_aggregated = 0
        self.records_excluded_as_totals = 0
        self.records_outside_every_period = 0
        #: A SCATS-style detector volume (schema.py's ``DetectorCountData``)
        #: has no turning movement, so it is counted separately from the
        #: movement-level counters above rather than folded into them — see
        #: :meth:`add_detector_counts`.
        self.detector_records_seen = 0
        self.detector_records_aggregated = 0
        self.detector_records_outside_every_period = 0
        self.first_interval_start: datetime | None = None
        self.last_interval_end: datetime | None = None
        self.interval_minutes: set[float] = set()

    def note_site(self, site_id: str, site_name: str | None) -> None:
        if site_name or site_id not in self._site_names:
            self._site_names[site_id] = site_name

    @staticmethod
    def _new_site_bucket(start: datetime, end: datetime) -> dict[str, Any]:
        """Every site bucket carries BOTH the turning-count fields and the
        detector-count fields from creation, regardless of which kind of
        record created it — so whichever of :meth:`add`/:meth:`add_detector_counts`
        runs second finds them already there instead of branching on which
        path got there first."""
        return {
            "total": 0.0,
            "records": 0,
            "movements": {},
            "intervals": set(),
            "first_interval_start": start,
            "last_interval_end": end,
            "detector_total": 0.0,
            "detector_records": 0,
            "detectors": {},
        }

    def add(self, counts: Iterable[TurningCountData]) -> None:
        for count in counts:
            self.records_seen += 1
            start = count.interval_start
            end = count.interval_end
            if self.first_interval_start is None or start < self.first_interval_start:
                self.first_interval_start = start
            if self.last_interval_end is None or end > self.last_interval_end:
                self.last_interval_end = end
            self.interval_minutes.add(round(count.interval_minutes, 6))

            if not is_summable(count):
                self.records_excluded_as_totals += 1
                continue

            matched = False
            for period in self._periods:
                # The same half-open window predicate demand seeding uses:
                # a record belongs to the period its interval STARTS in, so no
                # record can land in two windows and none is split across them.
                if not (period.start <= start < period.end):
                    continue
                matched = True
                self.records_aggregated += 1
                sites = self._by_period[period.name]
                site = sites.get(count.site_id)
                if site is None:
                    site = self._new_site_bucket(start, end)
                    sites[count.site_id] = site
                site["total"] += count.volume
                site["records"] += 1
                site["intervals"].add(start)
                if start < site["first_interval_start"]:
                    site["first_interval_start"] = start
                if end > site["last_interval_end"]:
                    site["last_interval_end"] = end
                key = movement_id(count)
                movement = site["movements"].get(key)
                if movement is None:
                    site["movements"][key] = {"volume": count.volume, "records": 1}
                else:
                    movement["volume"] += count.volume
                    movement["records"] += 1
            if not matched:
                self.records_outside_every_period += 1

    def add_detector_counts(self, counts: Iterable["DetectorCountData"]) -> None:
        """Fold per-detector loop volumes into a SITE TOTAL — never a movement.

        A detector volume has no turning movement (``schema.py``'s own reason
        for giving it a separate record type from ``TurningCountData``), so
        unlike :meth:`add` this never touches a site's ``movements`` dict.
        What it DOES add is a parallel ``detector_total``/``detectors``
        breakdown, so a site fed by loop detectors (the WA/SCATS adapter,
        which never emits ``TurningCountData`` at all) still gets an honest
        node-level comparison: :func:`observed_side` reports its total from
        ``detector_total`` and leaves ``directions`` empty for it — which is
        exactly correct, not a smaller version of the movement-level case.
        The ONLY way a movement number could legitimately appear from a SCATS
        drop is ``adapters/scats_wa.build_turning_counts_from_detector_map``,
        which requires a caller-supplied detector→movement map and produces
        real ``TurningCountData`` that :meth:`add` already handles; nothing is
        guessed here.
        """
        for count in counts:
            self.detector_records_seen += 1
            start = count.interval_start
            end = count.interval_end
            if self.first_interval_start is None or start < self.first_interval_start:
                self.first_interval_start = start
            if self.last_interval_end is None or end > self.last_interval_end:
                self.last_interval_end = end
            self.interval_minutes.add(round(count.interval_minutes, 6))

            matched = False
            for period in self._periods:
                if not (period.start <= start < period.end):
                    continue
                matched = True
                self.detector_records_aggregated += 1
                sites = self._by_period[period.name]
                site = sites.get(count.site_id)
                if site is None:
                    site = self._new_site_bucket(start, end)
                    sites[count.site_id] = site
                site["detector_total"] += count.volume
                site["detector_records"] += 1
                site["intervals"].add(start)
                if start < site["first_interval_start"]:
                    site["first_interval_start"] = start
                if end > site["last_interval_end"]:
                    site["last_interval_end"] = end
                detector = site["detectors"].get(count.detector_id)
                if detector is None:
                    site["detectors"][count.detector_id] = {
                        "volume": count.volume,
                        "records": 1,
                    }
                else:
                    detector["volume"] += count.volume
                    detector["records"] += 1
            if not matched:
                self.detector_records_outside_every_period += 1

    # -- output ------------------------------------------------------------

    def site_ids(self) -> list[str]:
        return sorted(self._site_names)

    def aggregates(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for period in self._periods:
            sites = self._by_period[period.name]
            out[period.name] = {
                site_id: {
                    "site_id": site_id,
                    "site_name": self._site_names.get(site_id),
                    "total": _round_volume(entry["total"]),
                    "records": entry["records"],
                    "interval_count": len(entry["intervals"]),
                    "first_interval_start": entry["first_interval_start"].isoformat(),
                    "last_interval_end": entry["last_interval_end"].isoformat(),
                    "movements": {
                        key: {
                            "volume": _round_volume(value["volume"]),
                            "records": value["records"],
                        }
                        for key, value in sorted(entry["movements"].items())
                    },
                    # Detector-loop side: a PARALLEL total, never summed into
                    # "total" above (turning counts and detector loops are two
                    # independent measurement methods; see observed_side()'s
                    # "mixed count basis" handling for why a site with both is
                    # excluded rather than silently added together).
                    "detector_total": (
                        _round_volume(entry["detector_total"])
                        if entry.get("detector_records")
                        else None
                    ),
                    "detector_records": entry.get("detector_records", 0),
                    "detectors": {
                        key: {
                            "volume": _round_volume(value["volume"]),
                            "records": value["records"],
                        }
                        for key, value in sorted((entry.get("detectors") or {}).items())
                    },
                    "count_basis": sorted(
                        ({"turning_counts"} if entry["records"] else set())
                        | ({"detector_counts"} if entry.get("detector_records") else set())
                    ),
                }
                for site_id, entry in sorted(sites.items())
            }
        return out

    def period_coverage(self) -> list[dict[str, Any]]:
        coverage: list[dict[str, Any]] = []
        for period in self._periods:
            sites = self._by_period[period.name]
            coverage.append(
                {
                    "name": period.name,
                    "start": period.start.isoformat(),
                    "end": period.end.isoformat(),
                    "site_count": len(sites),
                    "sites_with_data": sorted(sites),
                    # Named, not merely counted: a declared period that caught
                    # nothing is a fact about the survey the modeller has to
                    # see, not an empty dict to scroll past.
                    "sites_without_data": sorted(set(self._site_names) - set(sites)),
                    "total_volume": _round_volume(
                        sum(entry["total"] for entry in sites.values())
                    ),
                    "records": sum(entry["records"] for entry in sites.values()),
                    # Detector-loop side, reported separately from turning
                    # counts for the same reason ``aggregates()`` never sums
                    # them into "total_volume"/"records" above.
                    "detector_total_volume": _round_volume(
                        sum(entry.get("detector_total", 0.0) for entry in sites.values())
                    ),
                    "detector_records": sum(
                        entry.get("detector_records", 0) for entry in sites.values()
                    ),
                }
            )
        return coverage

    def coverage(self) -> dict[str, Any]:
        return {
            "first_interval_start": (
                self.first_interval_start.isoformat() if self.first_interval_start else None
            ),
            "last_interval_end": (
                self.last_interval_end.isoformat() if self.last_interval_end else None
            ),
            "interval_minutes": sorted(self.interval_minutes),
            "records_read": self.records_seen,
            "records_aggregated": self.records_aggregated,
            "records_excluded_as_totals": self.records_excluded_as_totals,
            "records_outside_every_period": self.records_outside_every_period,
            # Detector-loop side (schema.py's DetectorCountData — a SCATS
            # site's loop volumes, which carry no turning movement). Kept
            # separate from the turning-count counters above rather than
            # folded in, so "how much did we aggregate" always answers
            # honestly which measurement basis it is talking about.
            "detector_records_read": self.detector_records_seen,
            "detector_records_aggregated": self.detector_records_aggregated,
            "detector_records_outside_every_period": self.detector_records_outside_every_period,
        }


def _round_volume(value: float) -> float:
    """Volumes are counts; six decimals kills float drift without rounding a
    real fractional figure (a PCU-weighted source would carry one) into a lie."""
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest_observation_set(
    files: Sequence[Path],
    *,
    name: str,
    periods: Sequence[ObservationPeriod],
    adapter_name: str | None = None,
    thresholds: QualityThresholds | None = None,
) -> dict[str, Any]:
    """Parse, gate, aggregate and store one observation set.

    Streaming by construction: each workbook is parsed on its own, gated,
    folded into the accumulator and released before the next is opened — see
    the module docstring for the 6.3 GB measurement that makes this mandatory.

    Returns the stored set record (aggregates included).

    Raises:
        ObservationRefusal: nothing was stored. ``code`` says why; the
            ``detail`` carries the gate report when the refusal came from a
            REFUSE-tier finding. Also raised (as ``observation_workbook_unreadable``)
            when a staged workbook that opened fine at upload time and sniffed
            fine (an adapter's ``sniff`` only reads enough to score confidence,
            never the whole file) turns out unreadable once ``adapter.parse``
            walks every row — a corrupt/truncated body deeper in the file, or
            the xlsx decompression-bomb guard tripping mid-parse
            (``services/dataimport/xlsx_guard.py``). Mirrors
            ``services.genesis_jobs.stage_counts``'s ``AdapterError`` ->
            refusal mapping for the same adapter contract.
    """
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise ObservationRefusal(
            "observation_set_name_required",
            {
                "ko": "관측 세트 이름이 필요합니다.",
                "en": "An observation set name is required.",
                "ja": "観測セット名が必要です。",
            },
        )
    if len(cleaned_name) > MAX_SET_NAME_CHARS:
        raise ObservationRefusal(
            "observation_set_name_too_long",
            {
                "ko": f"관측 세트 이름은 최대 {MAX_SET_NAME_CHARS}자입니다.",
                "en": f"An observation set name may be at most {MAX_SET_NAME_CHARS} characters.",
                "ja": f"観測セット名は最大{MAX_SET_NAME_CHARS}文字です。",
            },
        )

    paths = list(files)
    if not paths:
        raise ObservationRefusal(
            "observation_no_files",
            {
                "ko": "가져올 파일이 없습니다.",
                "en": "No files were supplied to ingest.",
                "ja": "取り込むファイルがありません。",
            },
        )
    if len(paths) > MAX_FILES_PER_SET:
        raise ObservationRefusal(
            "observation_too_many_files",
            {
                "ko": f"한 세트에 최대 {MAX_FILES_PER_SET}개 파일까지 가져올 수 있습니다"
                f"(요청 {len(paths)}개).",
                "en": f"At most {MAX_FILES_PER_SET} files may be ingested as one set "
                f"({len(paths)} were supplied).",
                "ja": f"1セットあたり最大{MAX_FILES_PER_SET}ファイルです(要求{len(paths)}件)。",
            },
            detail={"file_count": len(paths)},
        )

    declared = _validate_periods(periods)
    adapter = _resolve_adapter(paths, adapter_name)

    aggregator = _Aggregator(declared)
    issues: list[ImportIssue] = []
    source_files: list[str] = []

    for path in sorted(paths, key=lambda item: item.name):
        try:
            parsed = adapter.parse([path])
        except AdapterError as exc:
            # The adapter's contract (services/dataimport/base.py) reserves
            # AdapterError for "unreadable at all" -- not a data-quality
            # finding, which would come back as a REFUSE-tier ImportIssue
            # instead and be handled by the gate branch below. Both
            # sniff() (adapter selection, above) and stage_workbook()'s
            # upload-time open check read far less of the file than a full
            # parse() does, so a workbook that cleared both can still fail
            # here -- same gap services.genesis_jobs.stage_counts already
            # closes for the genesis intake path; this mirrors it so the two
            # intake paths refuse the same way instead of one of them 500ing.
            raise ObservationRefusal(
                "observation_workbook_unreadable",
                {
                    "ko": f"'{path.name}'을(를) 읽을 수 없습니다: {exc}",
                    "en": f"{path.name!r} could not be read: {exc}",
                    "ja": f"'{path.name}' を読み込めませんでした: {exc}",
                },
                status=400,
                detail={"filename": path.name, "adapter": _adapter_payload(adapter)},
            ) from exc
        result: ImportResult | None = apply_quality_gate(parsed, thresholds)
        issues.extend(result.issues)
        source_files.extend(result.source_files or [path.name])
        for site in result.sites:
            aggregator.note_site(site.site_id, site.site_name)
        aggregator.add(result.counts)
        aggregator.add_detector_counts(result.detector_counts)
        # Release this workbook's records before the next one is opened. The
        # explicit clear is belt-and-braces (the local is rebound on the next
        # iteration anyway) and makes the intent unmissable to whoever edits
        # this loop next: the whole drop is NEVER alive at once.
        result.counts.clear()
        result.detector_counts.clear()
        result = None

    refusals = [issue for issue in issues if issue.severity is IssueSeverity.REFUSE]
    gate = _gate_summary(issues)
    if refusals:
        raise ObservationRefusal(
            "observation_gate_refused",
            {
                "ko": f"데이터 품질 게이트가 거부 {len(refusals)}건을 보고했으므로 관측 세트를 "
                "저장하지 않았습니다. 거부 사유를 그대로 전달합니다 — 거부된 데이터를 저장하면 "
                "이후 대조가 잘못된 숫자를 근거로 삼게 됩니다.",
                "en": f"The data-quality gate raised {len(refusals)} refusal(s), so nothing was "
                "stored. The findings are passed through as they are: storing refused data would "
                "let a later reality check grade a run against numbers we already know are wrong.",
                "ja": f"データ品質ゲートが拒否{len(refusals)}件を報告したため、観測セットは保存"
                "していません。理由をそのまま伝えます — 拒否されたデータを保存すると、以降の"
                "対照が誤った数値を根拠にしてしまいます。",
            },
            detail={
                "gate": gate,
                "source_files": sorted(set(source_files)),
                "adapter": _adapter_payload(adapter),
            },
        )

    if not aggregator.records_aggregated and not aggregator.detector_records_aggregated:
        raise ObservationRefusal(
            "observation_no_counts_in_declared_periods",
            {
                "ko": "선언한 시간대 안에 들어오는 교통량 기록이 하나도 없습니다. 빈 세트를 "
                "저장하면 '관측했지만 0'과 '관측하지 않음'을 구분할 수 없게 됩니다.",
                "en": "Not one count record falls inside the declared period(s). Storing an empty "
                "set would make 'counted, and it was zero' indistinguishable from 'never counted'.",
                "ja": "宣言した時間帯に入る交通量レコードが一件もありません。空のセットを保存すると"
                "「観測して0」と「観測していない」を区別できなくなります。",
            },
            detail={
                "gate": gate,
                "coverage": aggregator.coverage(),
                "periods": aggregator.period_coverage(),
                "source_files": sorted(set(source_files)),
            },
        )

    record = {
        "id": uuid4().hex[:12],
        "name": cleaned_name,
        "workspace_id": workspaces.current_workspace_id(),
        "ingested_at": _now_iso(),
        "adapter": _adapter_payload(adapter),
        "source_files": sorted(set(source_files)),
        "site_count": len(aggregator.site_ids()),
        "site_ids": aggregator.site_ids(),
        "coverage": aggregator.coverage(),
        "periods": aggregator.period_coverage(),
        "gate": gate,
        "aggregates": aggregator.aggregates(),
    }
    def store(sets: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        sets[record["id"]] = record
        return record, True

    _mutate_registry(store)
    logger.info(
        "observations: stored set %s (%s) — %d file(s), %d site(s), %d turning-count record(s) + "
        "%d detector-count record(s) aggregated, gate warn=%d info=%d",
        record["id"],
        cleaned_name,
        len(record["source_files"]),
        record["site_count"],
        aggregator.records_aggregated,
        aggregator.detector_records_aggregated,
        gate["counts"]["warn"],
        gate["counts"]["info"],
    )
    return record


def _validate_periods(periods: Sequence[ObservationPeriod]) -> list[ObservationPeriod]:
    declared = list(periods or [])
    if not declared:
        raise ObservationRefusal(
            "observation_periods_required",
            {
                "ko": "시간대를 최소 하나 선언해야 합니다. 5주치 조사를 한 덩어리로 합산해 "
                "1시간짜리 실행과 비교하면 수요 오차를 만들어 냅니다 — 어느 구간이 모델링 "
                "대상인지는 모델러의 결정이며 여기서 추측하지 않습니다.",
                "en": "At least one period must be declared. Summing a five-week survey into one "
                "figure and comparing it to a one-hour run would manufacture a demand error — "
                "which window represents the modelled period is the modeller's decision and is "
                "not guessed here.",
                "ja": "時間帯を最低一つ宣言してください。5週間分の調査を一括で合算して1時間の"
                "実行と比較すると需要誤差を作り出します — どの区間が対象かはモデラーの決定であり、"
                "ここで推測はしません。",
            },
        )
    if len(declared) > MAX_PERIODS_PER_SET:
        raise ObservationRefusal(
            "observation_too_many_periods",
            {
                "ko": f"시간대는 최대 {MAX_PERIODS_PER_SET}개까지 선언할 수 있습니다.",
                "en": f"At most {MAX_PERIODS_PER_SET} periods may be declared.",
                "ja": f"時間帯は最大{MAX_PERIODS_PER_SET}件まで宣言できます。",
            },
        )
    names = [period.name for period in declared]
    duplicates = sorted({item for item in names if names.count(item) > 1})
    if duplicates:
        raise ObservationRefusal(
            "observation_duplicate_period_name",
            {
                "ko": f"시간대 이름이 중복됩니다: {', '.join(duplicates)}. 같은 이름의 두 창을 "
                "합치면 어느 쪽 숫자인지 알 수 없게 됩니다.",
                "en": f"Duplicate period name(s): {', '.join(duplicates)}. Merging two windows "
                "under one name would make it impossible to say which one a number came from.",
                "ja": f"時間帯名が重複しています: {', '.join(duplicates)}。同名の二つの窓を"
                "統合すると、どちらの数値か分からなくなります。",
            },
            detail={"duplicates": duplicates},
        )
    return declared


def _resolve_adapter(paths: Sequence[Path], adapter_name: str | None) -> DataAdapter:
    if adapter_name:
        adapter = get_adapter(adapter_name)
        if adapter is None:
            known = sorted(item.info.name for item in list_adapters())
            raise ObservationRefusal(
                "observation_adapter_unknown",
                {
                    "ko": f"'{adapter_name}' 어댑터를 찾을 수 없습니다(등록된 어댑터: "
                    f"{', '.join(known)}).",
                    "en": f"No adapter named {adapter_name!r} is registered (registered: "
                    f"{', '.join(known)}).",
                    "ja": f"'{adapter_name}' というアダプタは登録されていません(登録済み: "
                    f"{', '.join(known)})。",
                },
                status=400,
                detail={"known_adapters": known},
            )
        return adapter

    adapter, scores = select_adapter(list(paths))
    if adapter is None:
        raise ObservationRefusal(
            "observation_adapter_not_recognised",
            {
                "ko": "이 파일들을 읽을 수 있는 어댑터가 없습니다. 확신도가 임계값에 못 미치는 "
                "어댑터를 골라 잘못 매핑하느니, 인식하지 못했다고 말합니다.",
                "en": "No registered adapter recognises these files. Telling you we do not "
                "recognise them beats picking the least-bad adapter and mis-mapping every column.",
                "ja": "これらのファイルを読めるアダプタがありません。確信度が閾値に満たない"
                "アダプタを選んで誤マッピングするより、認識できないと伝えます。",
            },
            status=400,
            detail={
                "sniff_scores": [
                    {"adapter": adapter_id, "confidence": result.confidence, "reason": result.reason}
                    for adapter_id, result in scores
                ]
            },
        )
    return adapter


def _adapter_payload(adapter: DataAdapter) -> dict[str, Any]:
    info = adapter.info
    return {
        "name": info.name,
        "version": info.version,
        "region": info.region,
        "source_system": info.source_system,
    }


def _gate_summary(issues: Sequence[ImportIssue]) -> dict[str, Any]:
    """Adapter + gate findings, counted by tier and carried through by code.

    WARN and INFO are stored, not flattened: 1.2's client finding that every
    workbook's final 23:45 bin is missing arrives as the adapter's
    ``daejeon_declared_window_not_covered`` WARN, and a set that dropped it
    would present a survey as complete that the client already knows is not.

    ``messages`` is passed through exactly as the producing layer left it —
    usually empty, because ``ImportIssue.messages`` is populated only where a
    finding has a written ko/en/ja form. Inventing translations of an adapter's
    English prose here would be manufacturing text nobody wrote; the envelope
    the router returns carries the ko/en/ja sentence instead.
    """
    counts = {"refuse": 0, "warn": 0, "info": 0}
    for issue in issues:
        counts[issue.severity.value] += 1
    stored = [
        {
            "severity": issue.severity.value,
            "code": issue.code,
            "message": issue.message,
            "messages": dict(issue.messages),
            "subject": issue.subject,
            "citation": issue.citation,
            "context": issue.context,
        }
        for issue in issues[:MAX_STORED_ISSUES]
    ]
    return {
        "counts": counts,
        "codes": sorted({issue.code for issue in issues}),
        "issues": stored,
        "issues_truncated": max(0, len(issues) - MAX_STORED_ISSUES),
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def summarize(record: Mapping[str, Any]) -> dict[str, Any]:
    """A set record without its aggregates — what a listing returns."""
    return {key: value for key, value in record.items() if key != "aggregates"}


def list_observation_sets() -> list[dict[str, Any]]:
    """Every set in the CURRENT workspace, newest first. Summaries only."""
    return sorted(
        (summarize(record) for record in _read_sets().values()),
        key=lambda record: str(record.get("ingested_at") or ""),
        reverse=True,
    )


def get_observation_set(set_id: str) -> dict[str, Any] | None:
    """One set with its aggregates, or ``None``. Never raises for a bad id."""
    if not set_id or not _SET_ID_RE.match(set_id):
        return None
    return _read_sets().get(set_id)


def delete_observation_set(set_id: str) -> bool:
    """Remove one set. Returns False when it was not there."""
    if not set_id or not _SET_ID_RE.match(set_id):
        return False

    def mutate(sets: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
        existed = sets.pop(set_id, None) is not None
        return existed, existed

    return _mutate_registry(mutate)


# ---------------------------------------------------------------------------
# The observed side for the reality check
# ---------------------------------------------------------------------------


def observed_side(
    set_id: str,
    *,
    period: str | None = None,
    site_nodes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build ``{node_id: NodeRealCounts}`` from a stored set, honestly.

    ``site_nodes`` is ``{observation site id: Vissim node id}`` and comes from
    the CALLER, always. Daejeon site ids are intersection names taken from file
    names (1.2's documented deviation, WARN
    ``daejeon_site_id_from_file_name``); they are labels, not controller ids,
    and they do not match Vissim node ids. Guessing that join — by position, by
    string similarity, by ordering — would produce a confidently wrong
    comparison, which is the exact failure this project exists to prevent. So
    an absent map is a refusal that NAMES the sites needing one.

    Returns ``{"nodes", "problem", "period", "periods", "coverage"}``.
    ``problem`` is ``None`` when the observed side is usable, otherwise one of
    this module's codes. ``coverage`` always names both directions of the
    site-level join plus any conflicting map entry — it is built before any
    node is emitted, so it cannot be quietly skipped on a happy path.
    """
    record = get_observation_set(set_id)
    empty = {
        "nodes": {},
        "problem": SET_NOT_FOUND,
        "period": period,
        "periods": [],
        "coverage": _empty_site_coverage(),
    }
    if record is None:
        return empty

    aggregates = record.get("aggregates") or {}
    period_names = list(aggregates)
    if period is None:
        # No period named: only unambiguous when the set has exactly one. Two
        # periods and a silent pick would grade a run against whichever window
        # happened to sort first.
        if len(period_names) != 1:
            return {
                "nodes": {},
                "problem": PERIOD_ABSENT,
                "period": None,
                "periods": period_names,
                "coverage": _empty_site_coverage(),
            }
        period = period_names[0]
    if period not in aggregates:
        return {
            "nodes": {},
            "problem": PERIOD_ABSENT,
            "period": period,
            "periods": period_names,
            "coverage": _empty_site_coverage(),
        }

    sites = aggregates[period]
    coverage = _empty_site_coverage()
    coverage["observation_sites"] = sorted(sites)

    mapping = {str(key): str(value) for key, value in (site_nodes or {}).items()}
    if not mapping:
        coverage["sites_without_node_mapping"] = sorted(sites)
        return {
            "nodes": {},
            "problem": SITE_NODE_MAP_MISSING,
            "period": period,
            "periods": period_names,
            "coverage": coverage,
        }

    # Two count sites pointed at one node would silently merge two
    # intersections' traffic into one comparison; both are excluded and named.
    claimed: dict[str, list[str]] = {}
    for site_id in sorted(sites):
        node_id = mapping.get(site_id)
        if node_id:
            claimed.setdefault(node_id, []).append(site_id)
    conflicting = {node: names for node, names in claimed.items() if len(names) > 1}
    reserved = sorted(node for node in claimed if node in _RESERVED_NODE_IDS)

    nodes: dict[str, Any] = {}
    for site_id in sorted(sites):
        entry = sites[site_id]
        node_id = mapping.get(site_id)
        if not node_id:
            coverage["sites_without_node_mapping"].append(site_id)
            continue
        if node_id in conflicting:
            coverage["sites_with_conflicting_node_mapping"].append(f"{site_id}->{node_id}")
            continue
        if node_id in _RESERVED_NODE_IDS:
            coverage["sites_with_rejected_node_id"].append(f"{site_id}->{node_id}")
            continue

        # A site's counted volume comes from exactly ONE measurement basis.
        # Turning counts (movement-aware) and detector loops (site-total-only,
        # e.g. SCATS/WA) are independent methods that could disagree; summing
        # them silently would manufacture a number nobody measured. No adapter
        # in this repo produces both for the same site today, so this branch
        # is a refusal for a combination that has never been observed, not a
        # smoothed-over one.
        has_turning = bool(entry.get("records"))
        has_detector = bool(entry.get("detector_records"))
        if has_turning and has_detector:
            coverage["sites_with_mixed_count_basis"].append(site_id)
            continue

        if has_detector:
            # A SCATS-style detector volume carries no turning movement (see
            # observations._Aggregator.add_detector_counts) — the node's total
            # IS the observation, and "directions" stays empty rather than
            # smuggling detector ids in as if they were movements.
            total_real = _as_int(entry.get("detector_total"))
            directions: list[dict[str, Any]] = []
            count_basis = "detector_counts"
        else:
            total_real = _as_int(entry.get("total"))
            directions = [
                {
                    "direction": mid,
                    "real": _as_int(volume.get("volume")) or 0,
                    "sim_basam": 0,
                }
                for mid, volume in sorted((entry.get("movements") or {}).items())
            ]
            count_basis = "turning_counts"

        nodes[node_id] = {
            "node_id": node_id,
            "node_name": entry.get("site_name") or site_id,
            "total_real": total_real,
            # The store holds no modelled number at all, by construction: the
            # modelled side comes from THIS run's movement_volumes (U5's whole
            # point), and reality_check blanks these slots anyway.
            "total_sim_basam": None,
            # What kind of comparison this node can honestly support:
            # "turning_counts" -> per-movement GEH is possible (directions is
            # populated); "detector_counts" -> node-TOTAL GEH only, no
            # per-movement number (directions is always []). A caller must not
            # read a populated "directions" from a "detector_counts" node —
            # this field exists so it never has to guess which case it is in.
            "count_basis": count_basis,
            "directions": directions,
        }
        coverage["sites_joined"].append(f"{site_id}->{node_id}")

    coverage["mapped_sites_absent_from_observations"] = sorted(
        site_id for site_id in mapping if site_id not in sites
    )
    coverage["sites_with_conflicting_node_mapping"].sort()
    coverage["sites_with_rejected_node_id"].sort()
    coverage["sites_with_mixed_count_basis"].sort()
    coverage["counts"] = {
        "observation_sites": len(sites),
        "joined": len(nodes),
        "unmapped": len(coverage["sites_without_node_mapping"]),
        "conflicting": len(coverage["sites_with_conflicting_node_mapping"]),
    }
    if reserved:
        logger.warning(
            "observations: refused node id(s) %s from a site->node map — they collide with the "
            "keys reality_check.observed_nodes uses to recognise a workbook payload",
            reserved,
        )

    return {
        "nodes": nodes,
        "problem": None if nodes else NO_MAPPED_SITES,
        "period": period,
        "periods": period_names,
        "coverage": coverage,
    }


def _empty_site_coverage() -> dict[str, Any]:
    return {
        "observation_sites": [],
        "sites_joined": [],
        "sites_without_node_mapping": [],
        "sites_with_conflicting_node_mapping": [],
        "sites_with_rejected_node_id": [],
        "sites_with_mixed_count_basis": [],
        "mapped_sites_absent_from_observations": [],
        "counts": {"observation_sites": 0, "joined": 0, "unmapped": 0, "conflicting": 0},
    }


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):  # pragma: no cover - stored values are numeric
        return None


# ---------------------------------------------------------------------------
# Registry I/O — unit U6's pattern, per workspace
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _mutate_registry(
    mutator: Callable[[dict[str, dict[str, Any]]], tuple[_T, bool]]
) -> _T:
    """THE read-modify-write funnel. Nothing else may call :func:`_write_payload`.

    The lock is held across read → mutate → write, so two threads cannot
    interleave their cycles and lose a set.

    Raises:
        RegistryUnavailableError: the current contents could not be read, so
            writing would destroy them.
    """
    with _REGISTRY_LOCK:
        path = registry_path()
        payload = _read_payload(path)
        result, changed = mutator(payload["sets"])
        if changed:
            _write_payload(path, payload)
        return result


def _read_sets() -> dict[str, dict[str, Any]]:
    """Sets for READ paths. Never raises — an unreadable registry degrades to
    "no sets" (a listing must not 500 over a transient share lock) and the
    state is disclosed by :func:`registry_health_status`."""
    try:
        return _read_payload(registry_path())["sets"]
    except RegistryUnavailableError as exc:
        logger.error(
            "observations: reading the registry at %s failed (%s); reporting NO observation sets "
            "for this call. The file is untouched and nothing will be written over it.",
            registry_path(),
            exc,
        )
        return {}


def _read_payload(path: Path) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        if not path.exists():
            return _empty_payload()

        raw: bytes | None = None
        last_error: OSError | None = None
        for attempt in range(_READ_RETRY_ATTEMPTS):
            try:
                raw = path.read_bytes()
                break
            except OSError as exc:
                last_error = exc
                if attempt + 1 < _READ_RETRY_ATTEMPTS:
                    time.sleep(_READ_RETRY_DELAY_S)
        if raw is None:
            raise RegistryUnavailableError(
                f"Could not read {path} after {_READ_RETRY_ATTEMPTS} attempts: "
                f"{type(last_error).__name__}: {last_error}"
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return _quarantine_and_restart(path, raw, exc)

        normalized = _normalize_payload(payload)
        if normalized is None:
            return _quarantine_and_restart(
                path, raw, ValueError("registry payload is not a {'sets': {...}} object")
            )
        _remember_state(path, normalized)
        return normalized


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """THE only writer: same-directory temp file -> fsync -> ``os.replace``.

    Same directory so the rename is same-volume (atomic on Windows too);
    ``fsync`` so a power loss cannot leave a renamed-but-empty registry. On any
    failure the temp file is removed and the live registry still holds its last
    good bytes — it was never opened for writing.
    """
    with _REGISTRY_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning(
                    "observations: could not remove the temp registry file %s after a failed "
                    "write; it is inert (the live registry is untouched).",
                    tmp_path,
                )
            raise
        _remember_state(path, payload)


def _empty_payload() -> dict[str, Any]:
    return {"sets": {}, "incidents": []}


def _normalize_payload(payload: Any) -> dict[str, Any] | None:
    """Coerce a parsed payload into this module's shape, or ``None`` when it is
    not a registry at all (which the caller treats as corruption).

    Entry-level repairs are non-destructive and minimal: a non-dict entry is
    dropped (nothing downstream can read one) and an entry missing ``id`` gets
    it back from its own key. A set whose aggregates are missing is left as it
    is — that is a partial record to disclose, not damage to repair.
    """
    if not isinstance(payload, dict):
        return None
    sets = payload.get("sets")
    if not isinstance(sets, dict):
        return None

    clean: dict[str, dict[str, Any]] = {}
    for key, record in sets.items():
        if not isinstance(record, dict):
            logger.warning(
                "observations: dropping unreadable registry entry %r (%s, not an object)",
                key,
                type(record).__name__,
            )
            continue
        if not record.get("id"):
            record = {**record, "id": str(key)}
        clean[str(key)] = record

    incidents = payload.get("incidents")
    clean_incidents = (
        [entry for entry in incidents if isinstance(entry, dict)]
        if isinstance(incidents, list)
        else []
    )
    return {"sets": clean, "incidents": clean_incidents[-_MAX_INCIDENTS:]}


def _quarantine_and_restart(path: Path, raw: bytes, exc: Exception) -> dict[str, Any]:
    """Move a corrupt registry aside, start a fresh one, record the loss.

    Never silently empty: the bytes are preserved verbatim at a
    ``.corrupt-<timestamp>`` sibling, the event is logged at ERROR, and it is
    written INTO the new registry so :func:`registry_health_status` can still
    disclose it after this process is gone.

    Raises:
        RegistryUnavailableError: the corrupt file could not be moved aside —
            we then refuse to write, because a fresh registry over it would
            destroy the only copy of those bytes.
    """
    key = str(path)
    lost = _last_known_record_count.get(key)
    try:
        quarantine_path = _quarantine_registry_file(path)
    except OSError as move_exc:
        raise RegistryUnavailableError(
            f"The observation registry at {path} did not parse ({type(exc).__name__}: {exc}) and "
            f"could not be moved aside for quarantine ({type(move_exc).__name__}: {move_exc}). "
            "Refusing to overwrite it — those bytes are the only copy of this workspace's "
            "observation sets."
        ) from move_exc

    incident = {
        "code": "registry_quarantined",
        "at": _now_iso(),
        "quarantine_path": str(quarantine_path),
        "corrupt_bytes": len(raw),
        # None means "unknown", never "zero": counting sets in bytes that do
        # not parse is impossible, and a guessed number is the invented figure
        # this project forbids.
        "lost_record_count": lost,
        "parse_error": f"{type(exc).__name__}: {exc}",
    }
    logger.error(
        "observations: the registry at %s is CORRUPT (%s: %s, %d bytes). Quarantined at %s and a "
        "fresh registry started. Sets lost: %s. Nothing was deleted — the original bytes are "
        "intact at the quarantine path.",
        path,
        type(exc).__name__,
        exc,
        len(raw),
        quarantine_path,
        lost if lost is not None else "unknown (first read since process start)",
    )

    payload = _empty_payload()
    payload["incidents"] = [incident]
    _write_payload(path, payload)
    return payload


def _quarantine_registry_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
        counter += 1
    os.replace(path, candidate)
    return candidate


def _remember_state(path: Path, payload: dict[str, Any]) -> None:
    key = str(path)
    _last_known_record_count[key] = len(payload.get("sets") or {})
    status = _health_from_incidents(payload.get("incidents") or [])
    if status is None:
        _registry_health.pop(key, None)
    else:
        _registry_health[key] = status


def _health_from_incidents(incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    quarantines = [entry for entry in incidents if entry.get("code") == "registry_quarantined"]
    if not quarantines:
        return None
    latest = quarantines[-1]
    lost = latest.get("lost_record_count")
    lost = int(lost) if isinstance(lost, int) else None
    quarantine_path = latest.get("quarantine_path")

    if lost is None:
        count_ko = "몇 건이 사라졌는지는 알 수 없습니다"
        count_en = "how many sets were lost is unknown"
        count_ja = "何件が失われたかは不明です"
    else:
        count_ko = f"관측 세트 {lost}건을 잃었습니다"
        count_en = f"{lost} observation set(s) were lost"
        count_ja = f"観測セット{lost}件が失われました"

    return {
        "code": "registry_quarantined",
        "at": latest.get("at"),
        "quarantine_path": quarantine_path,
        "lost_record_count": lost,
        "quarantine_count": len(quarantines),
        "messages": {
            "ko": (
                f"관측 데이터 목록 파일이 손상되어 격리했습니다. {count_ko}. 새 목록 파일로 다시 "
                f"시작했으며, 손상된 원본은 {quarantine_path} 에 그대로 보관되어 있습니다."
            ),
            "en": (
                f"The observation registry file was corrupt and has been quarantined; {count_en}. "
                f"A fresh registry was started and the original bytes are preserved at "
                f"{quarantine_path}."
            ),
            "ja": (
                f"観測データ一覧ファイルが破損していたため隔離しました。{count_ja}。新しい一覧"
                f"ファイルで再開し、破損した原本は {quarantine_path} にそのまま保存されています。"
            ),
        },
    }


def registry_health_status() -> dict[str, Any] | None:
    """The current workspace's quarantine disclosure, or ``None`` when healthy.

    An in-memory dict lookup, no disk I/O — same shape and the same reason as
    ``simulation_runs.registry_health_status``.
    """
    return _registry_health.get(str(registry_path()))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "MAX_FILES_PER_SET",
    "MAX_PERIODS_PER_SET",
    "MOVEMENT_ID_SEPARATOR",
    "NO_MAPPED_SITES",
    "PERIOD_ABSENT",
    "SET_NOT_FOUND",
    "SITE_NODE_MAP_MISSING",
    "WORKBOOK_SUFFIXES",
    "ObservationPeriod",
    "ObservationRefusal",
    "ObservationStoreError",
    "RegistryUnavailableError",
    "StagedWorkbookNotFoundError",
    "delete_observation_set",
    "discard_staged_workbook",
    "get_observation_set",
    "ingest_observation_set",
    "is_summable",
    "list_observation_sets",
    "movement_id",
    "observations_dir",
    "observed_side",
    "registry_health_status",
    "registry_path",
    "staged_workbook_path",
    "stage_workbook",
    "staging_dir",
    "summarize",
]

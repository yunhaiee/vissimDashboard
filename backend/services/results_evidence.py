"""Tri-state evidence descriptor for Vissim result databases (Phase 2 unit U1).

**Why a descriptor at all.** A plain ``SELECT`` against an evaluation table
collapses two facts that mean opposite things into the same empty list:

* the table is **absent** — that evaluation type was never armed, so the run
  carries no opinion at all about queues/movements; and
* the table **exists and is empty** — the evaluation WAS armed, Vissim
  created the table, and it collected nothing.

Both were measured in this checkout, read-only, before this module was
written (see ``backend/tests/fixtures/result_evidence/*.json``, which is
extracted by ``backend/tests/test_agent_tools_run.py``'s capability-matrix
test and pins exactly this):

* the customer-model family (70 result .dbs) has
  ``MOVEMENT_EvaluationTimeInterval`` populated (62-67 rows) and no
  ``QUEUECOUNTER_EvaluationTimeInterval`` at all — its network arms
  ``<nodeResults collectData="true">`` and leaves ``<queues>`` off;
* the vendor-example family (48 of 66 result .dbs) has the inverse —
  ``QUEUECOUNTER_EvaluationTimeInterval`` with 25 rows and
  ``MOVEMENT_EvaluationTimeInterval`` present but with **zero** rows, because
  the agent job runner arms all four evaluation types on networks that have
  no nodes defined;
* a further 17 vendor .dbs carry ``SIMULATIONRUN`` alone.

Reporting all three as ``[]`` would let the system imply "no anomaly" for a
run where queue evidence was explicitly requested and none came back. Hence
the three states below, and hence the tools_run.py policy they drive:
``TABLE_ABSENT`` omits the run_result key entirely, ``ARMED_BUT_EMPTY``
keeps the key with an explicit empty-with-reason marker.

**Read-only by construction.** Every connection here is opened through a
``file:...?mode=ro`` URI. Nothing in this module writes, and it must stay
that way: the same functions are pointed at a developer's live
``runtime/`` result databases by the offline reproduction test.

**No COM.** Everything here is sqlite reading of an already-finished run.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------

TABLE_ABSENT = "TABLE_ABSENT"
ARMED_BUT_EMPTY = "ARMED_BUT_EMPTY"

_PRESENT_RE = re.compile(r"^PRESENT\((\d+)\)$")


def present(rows: int) -> str:
    """The third state, carrying its own row count: ``PRESENT(63)``."""
    return f"PRESENT({int(rows)})"


def rows_in(state: str | None) -> int | None:
    """Row count encoded in a ``PRESENT(n)`` state, else ``None``.

    ``None`` for both other states on purpose — "zero rows" is
    ``ARMED_BUT_EMPTY``'s meaning, not a number this function should hand
    back as if it had been counted.
    """
    match = _PRESENT_RE.match(str(state or ""))
    return int(match.group(1)) if match else None


def is_present(state: str | None) -> bool:
    return rows_in(state) is not None


# ---------------------------------------------------------------------------
# The evaluation tables a run can produce
# ---------------------------------------------------------------------------
#
# Names are the ones Vissim itself creates (mixed case). SQLite matches table
# names case-insensitively, but sqlite_master preserves the creating case, so
# every lookup here is folded before comparison and the descriptor is keyed by
# the canonical spelling below.
#
# Which .inpx <evaluation> child produces which table was measured in this
# checkout rather than assumed (see this module's docstring):
#   <nodeResults> -> MOVEMENT_EvaluationTimeInterval (+ ...IntervalClass)
#   <queues>      -> QUEUECOUNTER_EvaluationTimeInterval
# The mapping comment in services/vissim_com.py used to have those two
# crossed; it was corrected alongside this unit.

MOVEMENT_INTERVAL_TABLE = "MOVEMENT_EvaluationTimeInterval"
MOVEMENT_INTERVAL_CLASS_TABLE = "MOVEMENT_EvaluationTimeIntervalClass"
QUEUECOUNTER_INTERVAL_TABLE = "QUEUECOUNTER_EvaluationTimeInterval"

EVALUATION_TABLES: tuple[str, ...] = (
    "SIMULATIONRUN",
    MOVEMENT_INTERVAL_TABLE,
    MOVEMENT_INTERVAL_CLASS_TABLE,
    QUEUECOUNTER_INTERVAL_TABLE,
    "VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeInterval",
    "VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass",
    "VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass",
    "DATACOLLECTIONMEASUREMENT_EvaluationTimeIntervalClass",
    "LINKEVALSEGMENT_EvaluationTimeIntervalClass",
)


# ---------------------------------------------------------------------------
# The empty-with-reason marker
# ---------------------------------------------------------------------------
#
# ARMED_BUT_EMPTY must keep the run_result key present, but the key's VALUE
# has to stay FALSY: every postrun constraint that consumes one of these
# payloads guards with `if not payload: return None` and then either calls
# `.keys()` on it (queue_length_definition_not_portable, constraints.py) or
# iterates it expecting dicts (gridlock_reports_los_a,
# delay_measurement_truncated_by_queue). A truthy marker would therefore
# either manufacture a finding out of the marker's own fields or crash the
# check. So the marker is an EMPTY dict/list subclass that carries its reason
# as attributes.
#
# Consequence, stated plainly rather than hidden: json.dumps() of a marker
# yields `{}` / `[]` — the attributes do not survive serialisation. The
# machine-readable, JSON-durable copy of the same fact is
# ``run_result["evidence"][<table>] == "ARMED_BUT_EMPTY"``, which is exactly
# what the descriptor exists for. The marker is the in-process signal; the
# descriptor is the wire format.


class _EmptyEvidence:
    """Shared behaviour for the two empty-with-reason markers."""

    state: str
    reason: str
    source: str

    def _describe(self, *, state: str, reason: str, source: str) -> None:
        self.state = state
        self.reason = reason
        self.source = source

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"<{type(self).__name__} state={self.state!r} source={self.source!r} "
            f"reason={self.reason!r}>"
        )


class EmptyEvidenceMapping(dict, _EmptyEvidence):
    """An empty mapping that knows why it is empty."""

    def __init__(self, *, state: str, reason: str, source: str) -> None:
        super().__init__()
        self._describe(state=state, reason=reason, source=source)


class EmptyEvidenceList(list, _EmptyEvidence):
    """An empty sequence that knows why it is empty."""

    def __init__(self, *, state: str, reason: str, source: str) -> None:
        super().__init__()
        self._describe(state=state, reason=reason, source=source)


ARMED_BUT_EMPTY_REASON = (
    "the evaluation was armed and Vissim created this table, but the run "
    "collected zero rows -- this is measured absence of data, not absence of "
    "an anomaly"
)


def armed_but_empty_mapping(source: str) -> EmptyEvidenceMapping:
    return EmptyEvidenceMapping(
        state=ARMED_BUT_EMPTY, reason=ARMED_BUT_EMPTY_REASON, source=source
    )


def armed_but_empty_list(source: str) -> EmptyEvidenceList:
    return EmptyEvidenceList(
        state=ARMED_BUT_EMPTY, reason=ARMED_BUT_EMPTY_REASON, source=source
    )


# ---------------------------------------------------------------------------
# Read-only sqlite access
# ---------------------------------------------------------------------------


def connect_readonly(db_path: Path | str, *, immutable: bool = False) -> sqlite3.Connection:
    """Open ``db_path`` through a ``mode=ro`` URI — never read-write.

    ``immutable=True`` additionally promises sqlite the file cannot change
    underneath it, which is what lets a test point these readers at a
    developer's live ``runtime/`` result databases without so much as a
    lock file appearing next to them. It is NOT the default: for a job work
    dir the promise would not be ours to make.
    """
    # sqlite's URI parser wants forward slashes and percent-escaping; on
    # Windows a drive-letter path also needs a leading slash.
    text = Path(db_path).resolve().as_posix()
    if not text.startswith("/"):
        text = "/" + text
    text = text.replace("?", "%3f").replace("#", "%23")
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"file:{text}?{flags}", uri=True)


def result_db_paths(job_dir: Path | str) -> list[Path]:
    """Every result .db under a run's work dir, in a stable order.

    Same discovery rule as ``results_reader.get_runtime_results`` (recursive
    glob) — a multi-seed job writes one ``*.results/`` subtree per seed.
    """
    root = Path(job_dir)
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.db"))


def _table_names(conn: sqlite3.Connection) -> dict[str, str]:
    """``{folded name: actual name}`` for every table in the db."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row[0]).upper(): str(row[0]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# describe_result_db / describe_result_dir
# ---------------------------------------------------------------------------


def describe_result_db(db_path: Path | str, *, immutable: bool = False) -> dict[str, str]:
    """``{table: PRESENT(n) | ARMED_BUT_EMPTY | TABLE_ABSENT}`` for one .db.

    Every name in :data:`EVALUATION_TABLES` appears in the result — a table
    the db does not have is reported as ``TABLE_ABSENT``, never omitted, so
    the descriptor is a complete statement about the run rather than a
    partial one whose gaps have to be guessed at.
    """
    conn = connect_readonly(db_path, immutable=immutable)
    try:
        existing = _table_names(conn)
        described: dict[str, str] = {}
        for canonical in EVALUATION_TABLES:
            actual = existing.get(canonical.upper())
            if actual is None:
                described[canonical] = TABLE_ABSENT
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{actual}"').fetchone()[0]
            described[canonical] = present(count) if count else ARMED_BUT_EMPTY
        return described
    finally:
        conn.close()


def merge_descriptors(descriptors: Iterable[dict[str, str]]) -> dict[str, str]:
    """Fold per-.db descriptors into one statement about a whole run.

    A multi-seed run produces one .db per seed. Row counts add up; a table
    that is populated in ANY seed is ``PRESENT`` for the run; a table that
    exists but is empty everywhere stays ``ARMED_BUT_EMPTY``; only a table
    missing from every seed is ``TABLE_ABSENT``.
    """
    merged: dict[str, str] = {table: TABLE_ABSENT for table in EVALUATION_TABLES}
    totals: dict[str, int] = {}
    for descriptor in descriptors:
        for table, state in descriptor.items():
            merged.setdefault(table, TABLE_ABSENT)
            count = rows_in(state)
            if count is not None:
                totals[table] = totals.get(table, 0) + count
            elif state == ARMED_BUT_EMPTY and merged[table] == TABLE_ABSENT:
                merged[table] = ARMED_BUT_EMPTY
    for table, count in totals.items():
        merged[table] = present(count)
    return merged


def describe_result_dir(job_dir: Path | str, *, immutable: bool = False) -> dict[str, str]:
    """:func:`describe_result_db` across every .db in a run's work dir.

    An unreadable .db is logged and skipped rather than raising: evidence
    extraction must never be able to turn a completed run into a failed one.
    """
    descriptors: list[dict[str, str]] = []
    for db_path in result_db_paths(job_dir):
        try:
            descriptors.append(describe_result_db(db_path, immutable=immutable))
        except Exception:
            logger.exception("results_evidence: could not describe %s", db_path)
    return merge_descriptors(descriptors)


# ---------------------------------------------------------------------------
# Row readers
# ---------------------------------------------------------------------------

_NODE_TOKEN_RE = re.compile(r"^(\d+)")


def node_token(object_id: object) -> str:
    """The node id a Vissim movement OBJECT_ID belongs to.

    Movement ids look like ``1-508-100.779980-510-62.109716`` — node, from
    link, from position, to link, to position. The node-level aggregate row
    Vissim writes alongside them is just ``1``. Splitting on ``-`` is not
    safe (positions can be negative); the leading integer run is.
    """
    text = str(object_id or "").strip()
    match = _NODE_TOKEN_RE.match(text)
    return match.group(1) if match else text


def _rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []


def read_queue_rows(
    db_paths: Iterable[Path], *, immutable: bool = False
) -> dict[str, dict[str, Any]]:
    """Per-movement queue evidence keyed by OBJECT_ID.

    ``{OBJECT_ID: {"qlen": mean, "qlenmax": max, "samples": n}}`` from
    ``MOVEMENT_EvaluationTimeInterval`` — the table
    ``<nodeResults collectData="true">`` produces, and the one
    ``results_reader._read_db``'s existing queue SQL already reads. (Not
    ``QUEUECOUNTER_EvaluationTimeInterval``: re-pointing at that table would
    read zero rows on the only family of networks in this checkout that has
    queue data at all.)

    Aggregation, for the multi-row cases (several evaluation intervals, and
    several seeds' .dbs): QLEN is a mean *by definition* so it is averaged;
    QLENMAX is a maximum by definition so it is maximised. With a single
    interval and a single seed — the shape every fixture and every customer
    .db in this checkout actually has — both reduce to the stored value
    unchanged.
    """
    qlen_samples: dict[str, list[float]] = {}
    qlenmax: dict[str, float] = {}

    for db_path in db_paths:
        conn = connect_readonly(db_path, immutable=immutable)
        try:
            rows = _rows(
                conn,
                f'SELECT OBJECT_ID, QLEN, QLENMAX FROM "{MOVEMENT_INTERVAL_TABLE}"',
            )
        finally:
            conn.close()
        for object_id, qlen, qmax in rows:
            key = str(object_id)
            if qlen is not None:
                qlen_samples.setdefault(key, []).append(float(qlen))
            if qmax is not None:
                qlenmax[key] = max(qlenmax.get(key, float(qmax)), float(qmax))
            qlen_samples.setdefault(key, [])

    return {
        key: {
            "qlen": (sum(samples) / len(samples)) if samples else None,
            "qlenmax": qlenmax.get(key),
            "samples": len(samples),
        }
        for key, samples in sorted(qlen_samples.items())
    }


def read_movement_class_rows(
    db_paths: Iterable[Path], *, immutable: bool = False
) -> dict[str, dict[str, Any]]:
    """Per-movement volume and delay keyed by OBJECT_ID.

    ``{OBJECT_ID: {"vehs": total, "vehdelay": mean|None, "samples": n}}`` from
    ``MOVEMENT_EvaluationTimeIntervalClass``.

    Restricted to ``ARG_VEHICLECLASS = 'ALL'`` when such rows exist — real
    customer .dbs in this checkout carry up to six vehicle classes side by
    side (``ALL``, ``1001``, ``1002``, ``1003``, ``2002``, ``2003``), and
    summing across them would double-count every vehicle. When no ``ALL``
    row exists the unfiltered rows are used rather than reporting nothing.

    ``vehdelay`` stays ``None`` when Vissim recorded none. That is the normal
    shape for a zero-volume movement in real data (measured: 5 of 63 rows in
    the customer sample), and inventing a delay of 0.0 there would fabricate
    a LOS grade for a movement Vissim graded not at all.
    """
    vehs_total: dict[str, float] = {}
    delay_samples: dict[str, list[float]] = {}
    seen: set[str] = set()

    for db_path in db_paths:
        conn = connect_readonly(db_path, immutable=immutable)
        try:
            rows = _rows(
                conn,
                f"SELECT OBJECT_ID, VEHS, VEHDELAY FROM "
                f"\"{MOVEMENT_INTERVAL_CLASS_TABLE}\" WHERE ARG_VEHICLECLASS = 'ALL'",
            )
            if not rows:
                rows = _rows(
                    conn,
                    f'SELECT OBJECT_ID, VEHS, VEHDELAY FROM "{MOVEMENT_INTERVAL_CLASS_TABLE}"',
                )
        finally:
            conn.close()
        for object_id, vehs, vehdelay in rows:
            key = str(object_id)
            seen.add(key)
            if vehs is not None:
                vehs_total[key] = vehs_total.get(key, 0.0) + float(vehs)
            if vehdelay is not None:
                delay_samples.setdefault(key, []).append(float(vehdelay))

    result: dict[str, dict[str, Any]] = {}
    for key in sorted(seen):
        samples = delay_samples.get(key, [])
        result[key] = {
            "vehs": vehs_total.get(key),
            "vehdelay": (sum(samples) / len(samples)) if samples else None,
            "samples": len(samples),
        }
    return result


# ---------------------------------------------------------------------------
# LOS banding
# ---------------------------------------------------------------------------
#
# Same HCM signalised-intersection control-delay bands this codebase already
# uses in services/exporters.py and services/report_exports.py. Mirrored
# rather than imported: both of those are workbook-export modules that pull
# in openpyxl and the whole scenario/dataset stack, which an agent run
# result has no business importing.

_LOS_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (10.0, "A"),
    (20.0, "B"),
    (35.0, "C"),
    (55.0, "D"),
    (80.0, "E"),
)


def los_band(delay_s: float | None) -> str | None:
    """HCM LOS letter for a control delay in seconds, or ``None``.

    ``None`` in, ``None`` out — never ``"A"``. A movement Vissim did not
    grade must not come back carrying the best possible grade; that is the
    exact confusion ``gridlock_reports_los_a`` exists to catch.
    """
    if delay_s is None:
        return None
    try:
        delay = float(delay_s)
    except (TypeError, ValueError):
        return None
    for limit, grade in _LOS_THRESHOLDS:
        if delay <= limit:
            return grade
    return "F"


__all__ = [
    "ARMED_BUT_EMPTY",
    "ARMED_BUT_EMPTY_REASON",
    "EVALUATION_TABLES",
    "EmptyEvidenceList",
    "EmptyEvidenceMapping",
    "MOVEMENT_INTERVAL_CLASS_TABLE",
    "MOVEMENT_INTERVAL_TABLE",
    "QUEUECOUNTER_INTERVAL_TABLE",
    "TABLE_ABSENT",
    "armed_but_empty_list",
    "armed_but_empty_mapping",
    "connect_readonly",
    "describe_result_db",
    "describe_result_dir",
    "is_present",
    "los_band",
    "merge_descriptors",
    "node_token",
    "present",
    "read_movement_class_rows",
    "read_queue_rows",
    "result_db_paths",
    "rows_in",
]

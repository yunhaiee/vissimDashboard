"""Phase 1.3: the agent action ledger -> a "파라미터 변경 기록 + 근거" DOCX section.

**What the ledger IS (nothing new is invented here).** Every agent turn already
appends an append-only JSONL record stream to
``runtime/agent_sessions/<session_id>/trace_<session_id>.jsonl``
(``services/agent/trace.py``); the orchestrator writes ``plan`` /
``preflight`` / ``action_start`` / ``action_end`` / ``postwrite`` / ``postrun``
/ ``refusal`` / ``warning`` / ``user_confirm`` records around every dispatch.
That file IS the action ledger this module exports. This module only READS it
(it never writes to a session directory) and renders it as the report section
MRWA asks for -- "parameter change record + rationale"
(docs/product/tk-requirements-2026-07-31.md, R4.0).

**How a model change is recognized from the ledger alone.** Read-tier tools
also produce ``action_start``/``action_end`` records, so "an action happened"
is NOT enough to call something a parameter change. The classifier here uses
only signals the ledger itself carries, with no tool-registry import (an old
session's trace can name a tool that no longer exists in today's registry, and
that record must still export):

  - a ``preflight`` record for the same iteration => the action was GATED, i.e.
    write- or run-tier (``orchestrator.py`` consults the guardrail engine for
    those two tiers only; the read-tier branch never preflights);
  - a following ``postwrite`` record => it was a WRITE (model change);
  - a following ``postrun`` record => it was a simulation RUN, which changes no
    parameter and is therefore excluded from this section;
  - a gated action whose dispatch failed produces neither, so a small snapshot
    of the run tier's tool names (:data:`_RUN_TIER_TOOLS`, plus the ``run_``
    name prefix) is the only tiebreaker used, and only there.

**Before-values.** The ledger records the WRITTEN value (the ``plan`` /
``action_start`` params) but never the pre-write value: ``action_end`` carries
only ``{status, error_type}``, and the guardrail engine, which does see the
before/after shadow models, records constraint ids rather than values. So a
"변경 전" cell is filled ONLY by chaining within the same session -- the
previous APPLIED value of the same (tool, target, attribute) -- and is
explicitly rendered as "기록 없음" for the first change of any target. The
DOCX prints that rule as a footnote so no reader can mistake a blank for a
zero. (Recording the before-value in the trace is a follow-up for whoever
owns the orchestrator's trace payloads; it is deliberately not done here.)

**Rationale ("근거").** ``POST /api/assistant/agent/approve`` now accepts an
optional one-line ``reason`` and records it as a ``user_confirm`` record with
``stage="approved"`` (or ``"declined"``), carrying the approved tool/params.
An approval's reason is attached to the next matching action of that tool
(one approval authorizes exactly one dispatch -- the same rule
``gates.PreapprovedWriteGate`` enforces). Ledgers written before that field
existed simply have no such record, and their rationale cell reads "-": this
module is fully backward-compatible with reason-less history.

**Approver ("승인자", unit U8).** The same ``user_confirm`` record now also
carries an ``actor`` object -- ``{user_id, username, display_name, role}`` --
copied from the AUTHENTICATED session that resolved the approval
(``routers/assistant.py``, gated by ``routers/auth.require_role``). It travels
exactly where the rationale travels: onto the change the approval authorized
(Table 1) and onto the decision the human made (Table 2). Two rules, both
deliberate:

* **Fail closed on provenance.** :func:`normalize_actor` accepts ONLY the
  complete U8 shape with a known role. A ledger carrying anything else -- a
  half-written object, a hand-edited trace, a future schema this build does
  not understand -- renders the "기록 없음(인증 이전)" fallback rather than
  whatever string it found. This document's whole value is that a name in the
  approver column was authenticated by this system; printing an unverified one
  would be worse than printing none.
* **Records predating authentication print the fallback**, not a blank and
  never a guess (approved policy, roadmap row 2.5). Reason-less AND actor-less
  legacy ledgers keep folding byte-identically -- the field is purely
  additive.

**Determinism.** Given the same ledger, the rendered CONTENT (every paragraph
and every table cell) is identical run to run; the only wall-clock value in
the body is the explicit "생성 시각" line, which callers can pin via
``generated_at``. Byte-level .docx equality is NOT claimed: python-docx saves
through ``zipfile``, whose members are stamped with the current local time.

This module is stdlib-only at import time (python-docx and the shared report
styling helpers are imported lazily inside the render functions, matching
``services/report_exports.py``'s own stated convention).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from services.agent.planner import pick_language_text
from services.agent.sessions import SESSIONS_ROOT

logger = logging.getLogger(__name__)

#: Same hex-only, length-capped session-id shape ``routers/agent_downloads.py``
#: and ``routers/network.py`` enforce before a client-supplied id reaches path
#: construction. Duplicated (not imported from a router) on purpose: a service
#: module must not import a router, and this is the same small, deliberate
#: constant duplication ``session_store.py``'s docstring already accepts for
#: ``SESSIONS_ROOT``-adjacent values.
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")

#: Longest a single rendered table cell may get before it is elided. Params can
#: legitimately carry a 100-point polyline (``set_des_speed_distribution_points``)
#: or a whole ``apply_edit_plan`` step list; a report table must stay readable.
#: Elision is marked with "..." so a truncated cell is never mistaken for the
#: whole value.
_MAX_CELL_CHARS = 180

#: Param names that IDENTIFY the object a change targets rather than carrying a
#: changed value. Suffix-based (the registry's own naming convention across all
#: 60 write verbs: ``no``/``link_no``/``sc_no``/``lane_index``/``point_nos``…),
#: with two bare names that carry no suffix.
_ID_EXACT = frozenset({"no", "id"})
_ID_SUFFIXES = ("_no", "_nos", "_id", "_ids", "_index")

#: Units for value params. Suffix rules cover the registry's modern naming
#: (``pos_m``, ``gap_time_s``, ``speed_kmh``, ``volume_vph``, ``volume_pph``);
#: the exact-name table covers the older verbs that predate that convention.
_UNIT_BY_SUFFIX = (
    ("_kmh", "km/h"),
    ("_vph", "veh/h"),
    ("_pph", "ped/h"),
    ("_km", "km"),
    ("_m", "m"),
    ("_s", "s"),
)
_UNIT_BY_NAME = {
    "volume": "veh/h",
    "cycle": "s",
    "offset": "s",
    "seconds": "s",
}

#: Params the planner protocol injects but that are not part of the action
#: itself (``planner.py`` pops ``summary`` before dispatch; a defensive filter
#: here keeps a hand-written or older trace from leaking it into a table).
_NON_ACTION_PARAMS = frozenset({"summary"})

#: The run tier's tool names — a DELIBERATE snapshot (taken 2026-08-05 from
#: ``services/agent/tools_run.py``, ``tools_sweep.py`` and ``tools_check.py``),
#: not an import. This module classifies from the ledger alone and must not
#: depend on today's registry (see the module docstring): an old session's
#: trace can name a tool the registry has since dropped or renamed, and that
#: record must still export. The set is consulted in exactly ONE place — the
#: failed-dispatch fallback in :func:`_emit`, where neither a ``postwrite`` nor
#: a ``postrun`` record exists to classify from. ``load_check`` is the member
#: that matters here: it is run-tier (a full Vissim load costs a licence seat,
#: hence the gate) but carries no ``run_`` prefix, so the prefix heuristic
#: alone would file a failed load check as a parameter change.
_RUN_TIER_TOOLS = frozenset({"run_simulation", "run_demand_stress_sweep", "load_check"})

#: The roles an ``actor`` may name, with their rendered label. A DELIBERATE
#: local snapshot of ``services/auth.ROLES`` rather than an import — the same
#: rule the run-tier snapshot above follows (this module classifies from the
#: ledger alone and never imports the live system), plus one reason specific to
#: U8: a role name this build does not know is a role whose meaning it cannot
#: render, and the approver column must never print an unverifiable string. An
#: unknown role therefore fails :func:`normalize_actor` and prints the
#: "not recorded" fallback. Adding a role to ``services/auth.ROLES`` means
#: adding it here too — that is intended to be a conscious, reviewed edit to
#: an audit document's vocabulary.
_ROLE_LABEL: dict[str, dict[str, str]] = {
    "modeler": {"ko": "모델러", "en": "modeler", "ja": "モデラー"},
    "reviewer": {"ko": "검토자", "en": "reviewer", "ja": "査読者"},
    "admin": {"ko": "관리자", "en": "administrator", "ja": "管理者"},
}


# ---------------------------------------------------------------------------
# Ledger data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Approver:
    """The authenticated human a ``user_confirm`` record names (unit U8).

    Only ever built by :func:`normalize_actor`, so an instance of this class is
    itself the statement "this ledger carried a complete, well-formed U8 actor"
    — the renderer never has to re-validate, and nothing else can smuggle a
    half-shaped dict into the approver column.
    """

    user_id: int
    username: str
    display_name: str
    role: str


def normalize_actor(value: Any) -> Approver | None:
    """The ledger's ``actor`` object as an :class:`Approver`, or ``None``.

    Fail closed (see the module docstring): every field must be present, of the
    right type, non-blank, and the role must be one this build knows. Anything
    else — including ``None``, a bare username, or a shape from some later
    schema — is reported as "no authenticated approver" so the caller renders
    the fallback instead of an unverified name. Unknown EXTRA keys are ignored
    rather than rejected: they cannot put a name in the column, so refusing
    them would only make a future additive field break old exports.
    """
    if not isinstance(value, dict):
        return None
    user_id = value.get("user_id")
    # ``bool`` is an ``int`` subclass in Python; True must not become user 1.
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return None
    role = value.get("role")
    if not isinstance(role, str) or role not in _ROLE_LABEL:
        return None
    username = value.get("username")
    display_name = value.get("display_name")
    if not isinstance(username, str) or not isinstance(display_name, str):
        return None
    username, display_name = username.strip(), display_name.strip()
    if not username or not display_name:
        return None
    return Approver(user_id=user_id, username=username, display_name=display_name, role=role)


@dataclass(frozen=True)
class ChangeEntry:
    """One parameter change the ledger records, in the language-neutral shape
    the renderer localizes. ``outcome`` is one of:

    - ``"applied"``      -- dispatched ok and postwrite-verified (or, for a
      truncated ledger with no postwrite record, dispatched ok);
    - ``"rolled_back"``  -- postwrite refused it and the working copy was
      restored (``ArtifactManager.rollback_to``);
    - ``"failed"``       -- the dispatch itself errored, so nothing landed.
    """

    seq: int
    ts: float | None
    tool: str
    change_type: str
    target: str
    attribute: str
    after: str
    before: str | None
    outcome: str
    summary: str
    reason: str | None
    postwrite_recorded: bool
    postwrite_findings: tuple[str, ...]
    postwrite_disabled: tuple[str, ...]
    warnings: tuple[str, ...]
    error_type: str | None
    message: str | None
    #: U8: the authenticated human whose approval authorized this change, when
    #: the ledger recorded one. ``None`` for every pre-authentication record —
    #: and for every change that was never gated behind an approval at all.
    approver: Approver | None = None

    @property
    def when(self) -> str:
        return format_ts(self.ts)


@dataclass(frozen=True)
class RefusalEntry:
    """One refused / rolled-back / human-declined change. ``phase``:

    - ``"preflight"``  -- a guardrail constraint refused it BEFORE dispatch;
    - ``"postwrite"``  -- verification refused it after the write, which also
      rolled the working copy back (the matching :class:`ChangeEntry` carries
      outcome ``"rolled_back"``);
    - ``"dispatch"``   -- the tool layer rejected the call (invalid params,
      unknown/unsupported tool, duplicate call);
    - ``"tier"``       -- the tier gate refused it outright;
    - ``"declined"``   -- a human declined the approval (with their reason, if
      they gave one).
    """

    seq: int
    ts: float | None
    tool: str
    phase: str
    error_type: str | None
    message: str
    target: str
    summary: str
    reason: str | None
    #: U8: for a ``"declined"`` row, the authenticated human who declined it;
    #: for the other phases, the approver of the approval this refusal consumed
    #: (a rolled-back write carries the same approver as its change row).
    approver: Approver | None = None

    @property
    def when(self) -> str:
        return format_ts(self.ts)


@dataclass(frozen=True)
class AuditLedger:
    session_id: str
    changes: tuple[ChangeEntry, ...]
    refusals: tuple[RefusalEntry, ...]
    record_count: int
    source_filename: str | None = None

    @property
    def applied_count(self) -> int:
        return sum(1 for entry in self.changes if entry.outcome == "applied")

    @property
    def reasoned_count(self) -> int:
        """Changes carrying a human-supplied rationale line."""
        return sum(1 for entry in self.changes if entry.reason)

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.refusals


# ---------------------------------------------------------------------------
# Ledger location + reading
# ---------------------------------------------------------------------------


def resolve_session_dir(session_id: str, *, sessions_root: Path | None = None) -> Path | None:
    """The session's work dir, or ``None`` for anything that is not a clean,
    existing session directory. Mirrors ``routers/agent_downloads.py``'s
    ``_resolve_session_dir`` defense in depth: allowlist-validate the id, then
    re-check the resolved path really lands inside the sessions root. Never
    raises."""
    if not session_id or not _SESSION_ID_RE.match(session_id):
        return None

    root = (sessions_root or SESSIONS_ROOT).resolve()
    session_dir = (root / session_id).resolve()
    try:
        session_dir.relative_to(root)
    except ValueError:
        logger.warning("audit_trail: session_id %r resolved outside the sessions root; ignoring", session_id)
        return None
    if not session_dir.is_dir():
        return None

    # Unit 1.4: the ledger is a verbatim record of what the agent did to a
    # specific project's network -- link numbers, parameter values, the
    # planner's rationale. A session owned by another workspace is reported as
    # not existing at all (legacy sessions with no sidecar resolve to the
    # default workspace, so pre-1.4 audit exports keep working).
    from services import workspaces
    from services.agent.sessions import session_workspace_matches

    if not session_workspace_matches(
        session_id, workspaces.current_workspace_id(), sessions_root or SESSIONS_ROOT
    ):
        logger.info("audit_trail: session %s belongs to another workspace; hiding", session_id)
        return None
    return session_dir


def ledger_path(session_dir: Path, session_id: str) -> Path:
    """``<work_dir>/trace_<session_id>.jsonl`` — ``TraceWriter``'s own naming."""
    return Path(session_dir) / f"trace_{session_id}.jsonl"


def read_records(path: Path) -> list[dict[str, Any]]:
    """Read a trace file back, skipping unparseable lines.

    Deliberately NOT ``services.agent.trace.read_trace``: that one raises on
    the first malformed line, which would lose the whole audit trail over a
    single torn write (a process killed mid-append leaves exactly that). An
    audit export must render everything it can and say nothing about what it
    could not read, so bad lines are logged and skipped.
    """
    records: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    logger.warning("audit_trail: skipping unparseable ledger line %s:%d", path, lineno)
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        logger.warning("audit_trail: cannot read ledger %s", path, exc_info=True)
    return records


def read_ledger(session_id: str, *, sessions_root: Path | None = None) -> AuditLedger | None:
    """Load one session's ledger, or ``None`` when the session directory or its
    trace file does not exist (the caller turns that into a 404). A session
    that exists but recorded no parameter change returns an EMPTY ledger, not
    ``None`` — "this session changed nothing" is itself a valid audit
    statement and gets a real (empty-state) document."""
    session_dir = resolve_session_dir(session_id, sessions_root=sessions_root)
    if session_dir is None:
        return None
    path = ledger_path(session_dir, session_id)
    if not path.is_file():
        return None
    return build_ledger(session_id, read_records(path), source_filename=path.name)


# ---------------------------------------------------------------------------
# Record stream -> ledger entries
# ---------------------------------------------------------------------------


@dataclass
class _Iteration:
    """Mutable accumulator for one planner iteration's records.

    The record stream is strictly sequential (one ``plan`` record opens every
    iteration — ``orchestrator.py``'s loop writes it before anything else), so
    the fold below never has to key on ``iteration`` numbers, which restart at
    0 on every turn and would collide across a multi-turn session's single
    appended-to file.
    """

    ts: float | None
    tool: str | None
    params: dict[str, Any]
    summary: str
    preflight: dict[str, Any] | None = None
    #: When the write actually left for Vissim (the ``action_start`` record).
    #: The audit "when" column reports THIS, not the planning timestamp, and
    #: falls back to the ``plan`` record's ts only for an action that never
    #: dispatched.
    action_ts: float | None = None
    dispatched: bool = False
    status: str | None = None
    error_type: str | None = None
    postwrite: dict[str, Any] | None = None
    postrun: dict[str, Any] | None = None
    approval_requested: bool = False
    warnings: list[str] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)


def build_ledger(
    session_id: str, records: list[dict[str, Any]], *, source_filename: str | None = None
) -> AuditLedger:
    """Fold a trace record stream into :class:`AuditLedger`. Pure — no I/O, no
    clock, no registry — so a synthetic fixture exercises exactly the code a
    real session's file does."""
    changes: list[ChangeEntry] = []
    refusals: list[RefusalEntry] = []
    approvals: list[dict[str, Any]] = []
    current: _Iteration | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            _emit(current, approvals, changes, refusals)
        current = None

    for record in records:
        kind = record.get("kind")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        ts = record.get("ts")
        ts = float(ts) if isinstance(ts, (int, float)) else None

        if kind == "plan":
            close()
            current = _Iteration(
                ts=ts,
                tool=(payload.get("tool") or None),
                params=_clean_params(payload.get("params")),
                summary=str(payload.get("summary") or ""),
            )
            continue

        if kind == "user_confirm":
            stage = str(payload.get("stage") or "")
            if stage == "approved":
                approvals.append(
                    {
                        "tool": payload.get("tool"),
                        "reason": _clean_reason(payload.get("reason")),
                        "approver": normalize_actor(payload.get("actor")),
                        "consumed": False,
                    }
                )
            elif stage == "declined":
                refusals.append(
                    RefusalEntry(
                        seq=0,
                        ts=ts,
                        tool=str(payload.get("tool") or "-"),
                        phase="declined",
                        error_type=None,
                        message="",
                        target=_target_of(_clean_params(payload.get("params"))),
                        summary=str(payload.get("summary") or ""),
                        reason=_clean_reason(payload.get("reason")),
                        approver=normalize_actor(payload.get("actor")),
                    )
                )
            elif stage == "requested" and current is not None:
                current.approval_requested = True
            continue

        if current is None:
            continue

        if kind == "preflight":
            if payload.get("tool") == current.tool:
                current.preflight = payload
        elif kind == "action_start":
            current.dispatched = True
            current.action_ts = ts
            params = _clean_params(payload.get("params"))
            if params:
                current.params = params
        elif kind == "action_end":
            current.status = payload.get("status")
            current.error_type = payload.get("error_type")
        elif kind == "postwrite":
            current.postwrite = payload
        elif kind == "postrun":
            current.postrun = payload
        elif kind == "warning":
            current.warnings.extend(str(m) for m in (payload.get("messages") or []))
        elif kind == "refusal":
            current.refusals.append({**payload, "ts": ts})

    close()

    changes = [
        replace(entry, seq=index)
        for index, entry in enumerate(_chain_before_values(changes), start=1)
    ]
    refusals = [replace(entry, seq=index) for index, entry in enumerate(refusals, start=1)]
    return AuditLedger(
        session_id=session_id,
        changes=tuple(changes),
        refusals=tuple(refusals),
        record_count=len(records),
        source_filename=source_filename,
    )


def _emit(
    it: _Iteration,
    approvals: list[dict[str, Any]],
    changes: list[ChangeEntry],
    refusals: list[RefusalEntry],
) -> None:
    """Turn one finished iteration into 0..2 ledger entries."""
    if not it.tool:
        return
    if it.preflight is None:
        # Read-tier (or an ungated/unrecognized) action: never a model change.
        return
    if it.postrun is not None:
        # A simulation run changes no parameter — out of this section's scope.
        return

    target = _target_of(it.params)
    change_type = _change_type(it.tool)
    attribute, after = _attribute_and_value(it.params, change_type)

    if not it.dispatched:
        # Refused before dispatch, or paused for approval and never dispatched
        # in THIS iteration. A pure approval pause is deliberately dropped: the
        # approve turn replays the same plan+preflight+action, so counting the
        # pause too would double-count every approved change (and a proposal
        # the user declined is recorded by its own "declined" user_confirm
        # record instead).
        if not it.refusals:
            return
        blocking = it.refusals[0]
        refused_reason, refused_approver = _take_approval(approvals, it.tool)
        refusals.append(
            RefusalEntry(
                seq=0,
                ts=blocking.get("ts") or it.ts,
                tool=it.tool,
                phase=str(blocking.get("phase") or ("tier" if blocking.get("tier") else "dispatch")),
                error_type=blocking.get("error_type"),
                message=str(blocking.get("message") or ""),
                target=target,
                summary=it.summary,
                reason=refused_reason,
                approver=refused_approver,
            )
        )
        return

    if it.postwrite is None and (it.tool in _RUN_TIER_TOOLS or it.tool.startswith("run_")):
        # A run-tier action whose dispatch failed: no postrun record was ever
        # written, so the tool NAME is the only signal left that this was not a
        # write. The name snapshot catches ``load_check``, which the ``run_``
        # prefix does not; the prefix is kept as a forward-compatible secondary
        # heuristic for run verbs added after that snapshot was taken.
        return

    reason, approver = _take_approval(approvals, it.tool)
    postwrite = it.postwrite or {}
    postwrite_blocking = bool(postwrite.get("blocking"))
    dispatch_refusal = next((r for r in it.refusals if r.get("phase") == "dispatch"), None)
    postwrite_refusal = next((r for r in it.refusals if r.get("phase") == "postwrite"), None)

    if it.status == "ok" and postwrite_blocking:
        outcome = "rolled_back"
        message = str((postwrite_refusal or {}).get("message") or "")
        error_type = (postwrite_refusal or {}).get("error_type")
    elif it.status == "ok":
        outcome = "applied"
        message = None
        error_type = None
    else:
        outcome = "failed"
        message = str((dispatch_refusal or {}).get("message") or "")
        error_type = it.error_type or (dispatch_refusal or {}).get("error_type")

    changes.append(
        ChangeEntry(
            seq=0,
            ts=it.action_ts or it.ts,
            tool=it.tool,
            change_type=change_type,
            target=target,
            attribute=attribute,
            after=after,
            before=None,
            outcome=outcome,
            summary=it.summary,
            reason=reason,
            postwrite_recorded=it.postwrite is not None,
            postwrite_findings=tuple(str(f) for f in (postwrite.get("findings") or [])),
            postwrite_disabled=tuple(str(f) for f in (postwrite.get("disabled") or [])),
            warnings=tuple(it.warnings),
            error_type=error_type,
            message=message,
            approver=approver,
        )
    )

    if outcome == "rolled_back":
        refusals.append(
            RefusalEntry(
                seq=0,
                ts=(postwrite_refusal or {}).get("ts") or it.action_ts or it.ts,
                tool=it.tool,
                phase="postwrite",
                error_type=error_type,
                message=message or "",
                target=target,
                summary=it.summary,
                reason=reason,
                approver=approver,
            )
        )


def _take_approval(approvals: list[dict[str, Any]], tool: str) -> tuple[str | None, Approver | None]:
    """Consume the earliest unconsumed approval for ``tool`` and return its
    ``(reason, approver)``. One approval authorizes exactly one dispatch — the
    same rule ``gates.PreapprovedWriteGate`` enforces on the live path — so the
    rationale and the approver are taken together, from the SAME record: they
    are two halves of one human decision and must never end up describing
    different ones."""
    for approval in approvals:
        if approval["consumed"]:
            continue
        if approval.get("tool") and approval["tool"] != tool:
            continue
        approval["consumed"] = True
        return approval.get("reason"), approval.get("approver")
    return None, None


def _chain_before_values(changes: list[ChangeEntry]) -> list[ChangeEntry]:
    """Fill ``before`` from the previous APPLIED value of the same
    (tool, target, attribute) within this same session — the only before-value
    the ledger can honestly supply (see the module docstring). A first change,
    or one whose predecessor was rolled back / failed, keeps ``None``."""
    seen: dict[tuple[str, str, str], str] = {}
    out: list[ChangeEntry] = []
    for entry in changes:
        key = (entry.tool, entry.target, entry.attribute)
        before = seen.get(key) if entry.change_type == "set" else None
        out.append(replace(entry, before=before))
        if entry.outcome == "applied" and entry.change_type == "set":
            seen[key] = entry.after
    return out


# ---------------------------------------------------------------------------
# Param rendering
# ---------------------------------------------------------------------------


def _clean_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    return {str(k): v for k, v in params.items() if str(k) not in _NON_ACTION_PARAMS}


def _clean_reason(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def _is_id_param(name: str) -> bool:
    return name in _ID_EXACT or name.endswith(_ID_SUFFIXES)


def _change_type(tool: str) -> str:
    if tool.startswith("set_"):
        return "set"
    if tool.startswith("add_"):
        return "add"
    if tool.startswith("remove_") or tool.startswith("delete_"):
        return "remove"
    if tool == "apply_edit_plan":
        return "batch"
    if tool.startswith("rollback"):
        return "rollback"
    return "other"


def unit_for(name: str) -> str:
    """The unit a value param carries, or "" when it has none."""
    if name in _UNIT_BY_NAME:
        return _UNIT_BY_NAME[name]
    for suffix, unit in _UNIT_BY_SUFFIX:
        if name.endswith(suffix):
            return unit
    return ""


def _format_scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _elide(text: str) -> str:
    if len(text) <= _MAX_CELL_CHARS:
        return text
    return text[: _MAX_CELL_CHARS - 3].rstrip() + "..."


def _target_of(params: dict[str, Any]) -> str:
    ids = [f"{k}={_format_scalar(v)}" for k, v in params.items() if _is_id_param(k)]
    return _elide(", ".join(ids)) if ids else "-"


def _attribute_and_value(params: dict[str, Any], change_type: str) -> tuple[str, str]:
    """(항목, 변경 후) for one action's params.

    Identity params (``*_no``/``*_id``/``*_index``) name the target and are
    excluded here; what remains is what actually changed. Rendered as raw
    ``key=value`` pairs on purpose: a hand-maintained tool->label map across 60
    write verbs is exactly the kind of thing that silently drifts out of sync
    with the registry, and an audit table that is 3 releases stale is worse
    than one that is verbose. The tool column carries the semantics.
    """
    values = {k: v for k, v in params.items() if not _is_id_param(k)}
    if change_type in ("add", "remove", "batch", "rollback") or len(values) != 1:
        attribute = {
            "add": "객체 생성",
            "remove": "객체 삭제",
            "batch": "복합 편집",
            "rollback": "되돌리기",
        }.get(change_type, ", ".join(values) or "-")
        rendered = ", ".join(f"{k}={_format_scalar(v)}" for k, v in values.items()) or "-"
        return attribute, _elide(rendered)

    (name, value), = values.items()
    unit = unit_for(name)
    rendered = _format_scalar(value)
    return name, _elide(f"{rendered} {unit}".strip())


def format_ts(ts: float | None) -> str:
    """UTC, second resolution — the ledger's own ``time.time()`` stamp."""
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


# ---------------------------------------------------------------------------
# Localized strings (ko primary; pick_language_text falls back to ko)
# ---------------------------------------------------------------------------

SECTION_TITLE = {
    "ko": "파라미터 변경 기록 및 근거",
    "en": "Parameter Change Record and Rationale",
    "ja": "パラメータ変更記録と根拠",
}
_INTRO = {
    "ko": (
        "본 절은 AI 에이전트 세션의 액션 레저(append-only 기록)에서 자동 생성되었다. "
        "모델 파라미터를 실제로 변경한 동작만 수록하며, 조회 동작과 시뮬레이션 실행은 제외한다. "
        "각 행의 검증 결과는 쓰기 직후 가드레일 검사(postwrite)의 판정이다."
    ),
    "en": (
        "This section is generated from the AI agent session's append-only action ledger. "
        "It lists only actions that actually changed a model parameter; read-only lookups and "
        "simulation runs are excluded. The verification column is the postwrite guardrail verdict "
        "recorded immediately after each write."
    ),
    "ja": (
        "本節は AI エージェントセッションの追記型アクション台帳から自動生成された。"
        "モデルのパラメータを実際に変更した操作のみを収録し、参照操作とシミュレーション実行は除外する。"
        "検証欄は書き込み直後のガードレール検査(postwrite)の判定である。"
    ),
}
_META_LINE = {
    "ko": "세션 {session} · 레저 {source} · 기록 {records}건 · 생성 시각 {generated}",
    "en": "Session {session} · Ledger {source} · {records} records · Generated {generated}",
    "ja": "セッション {session} · 台帳 {source} · 記録 {records}件 · 生成時刻 {generated}",
}
_TABLE1_CAPTION = {
    "ko": "표 {n} - 승인·적용된 파라미터 변경",
    "en": "Table {n} - Approved parameter changes",
    "ja": "表 {n} - 承認・適用されたパラメータ変更",
}
_TABLE2_CAPTION = {
    "ko": "표 {n} - 거부·롤백·거절 기록",
    "en": "Table {n} - Refused, rolled back and declined changes",
    "ja": "表 {n} - 拒否・ロールバック・却下の記録",
}
#: U8 puts "승인자" immediately BEFORE "근거" in both tables, on purpose: the
#: two cells come from the same ``user_confirm`` record and answer "who allowed
#: this / on what grounds" as one thought. Keeping the rationale last also
#: leaves it the widest-reading trailing column it already was.
_TABLE1_HEAD = {
    "ko": ["#", "시각(UTC)", "대상", "항목", "변경 전 → 변경 후", "도구", "검증", "승인자", "근거"],
    "en": [
        "#", "Time (UTC)", "Target", "Attribute", "Before → After", "Tool", "Verification",
        "Approver", "Rationale",
    ],
    "ja": ["#", "時刻(UTC)", "対象", "項目", "変更前 → 変更後", "ツール", "検証", "承認者", "根拠"],
}
_TABLE2_HEAD = {
    "ko": ["#", "시각(UTC)", "도구", "대상", "단계", "사유", "승인자", "근거"],
    "en": [
        "#", "Time (UTC)", "Tool", "Target", "Phase", "Reason given by the system",
        "Approver", "Rationale",
    ],
    "ja": ["#", "時刻(UTC)", "ツール", "対象", "段階", "理由", "承認者", "根拠"],
}
_EMPTY_CHANGES = {
    "ko": "이 세션에는 적용된 파라미터 변경 기록이 없습니다.",
    "en": "This session recorded no applied parameter changes.",
    "ja": "このセッションには適用されたパラメータ変更の記録がありません。",
}
_EMPTY_REFUSALS = {
    "ko": "거부·롤백·거절된 변경이 없습니다.",
    "en": "No refused, rolled back or declined changes.",
    "ja": "拒否・ロールバック・却下された変更はありません。",
}
_SUMMARY = {
    "ko": "요약: 적용 {applied}건 · 롤백/실패 {other}건 · 거부·거절 {refused}건 · 근거 기입 {reasoned}건.",
    "en": "Summary: {applied} applied · {other} rolled back or failed · {refused} refused or declined · "
          "{reasoned} with a written rationale.",
    "ja": "要約: 適用 {applied}件 · ロールバック/失敗 {other}件 · 拒否・却下 {refused}件 · 根拠記入 {reasoned}件。",
}
_FOOTNOTE = {
    "ko": (
        "주) '변경 전' 값은 레저에 직접 기록되지 않으므로, 동일 세션 내에서 같은 대상·항목에 대한 "
        "직전 적용 값으로만 연결한다. 최초 변경은 '기록 없음'으로 표기한다."
    ),
    "en": (
        "Note: the ledger does not record pre-write values, so 'Before' is chained from the previous "
        "applied value of the same target/attribute within this session; the first change of any target "
        "is shown as 'not recorded'."
    ),
    "ja": (
        "注) 「変更前」の値は台帳に直接記録されないため、同一セッション内の同じ対象・項目に対する"
        "直前の適用値からのみ連結する。最初の変更は「記録なし」と表記する。"
    ),
}
_NOT_RECORDED = {"ko": "기록 없음", "en": "not recorded", "ja": "記録なし"}
#: U8: what the approver cell says when the ledger names no AUTHENTICATED
#: approver — every record written before roadmap 2.5's login existed, and any
#: record whose actor object does not pass :func:`normalize_actor`. It names
#: the cause so a reader never mistakes it for "nobody approved this".
_APPROVER_NOT_RECORDED = {
    "ko": "기록 없음(인증 이전)",
    "en": "not recorded (pre-authentication)",
    "ja": "記録なし(認証導入前)",
}
_NONE_MARK = {"ko": "-", "en": "-", "ja": "-"}
_OUTCOME = {
    "applied": {"ko": "검증 통과", "en": "verified", "ja": "検証合格"},
    "rolled_back": {"ko": "검증 실패 → 롤백", "en": "verification failed -> rolled back", "ja": "検証失敗 → ロールバック"},
    "failed": {"ko": "적용 실패", "en": "not applied (error)", "ja": "適用失敗"},
}
_CHECKS = {"ko": "검사 {n}건", "en": "{n} checks", "ja": "検査 {n}件"}
_WARNS = {"ko": "경고 {n}건", "en": "{n} warnings", "ja": "警告 {n}件"}
_NO_POSTWRITE = {"ko": "검증 기록 없음", "en": "no postwrite record", "ja": "検証記録なし"}
_PHASE = {
    "preflight": {"ko": "사전 검사", "en": "preflight", "ja": "事前検査"},
    "postwrite": {"ko": "사후 검증", "en": "postwrite", "ja": "事後検証"},
    "dispatch": {"ko": "호출 거부", "en": "dispatch", "ja": "呼び出し拒否"},
    "tier": {"ko": "권한 거부", "en": "tier gate", "ja": "権限拒否"},
    "declined": {"ko": "사용자 거절", "en": "declined by user", "ja": "ユーザー却下"},
}


def _t(table: dict[str, str], language: str) -> str:
    return pick_language_text(table, language)


def _verification_text(entry: ChangeEntry, language: str) -> str:
    parts = [_t(_OUTCOME.get(entry.outcome, _OUTCOME["failed"]), language)]
    if entry.outcome == "failed" and entry.error_type:
        parts[0] = f"{parts[0]} ({entry.error_type})"
    if entry.postwrite_recorded:
        parts.append(_t(_CHECKS, language).format(n=len(entry.postwrite_findings)))
    elif entry.outcome == "applied":
        parts.append(_t(_NO_POSTWRITE, language))
    if entry.warnings:
        parts.append(_t(_WARNS, language).format(n=len(entry.warnings)))
    if entry.outcome == "failed" and entry.message:
        # A failed change never reaches the refusal table (it WAS dispatched),
        # so this cell is the only place the reason it did not land is shown.
        parts.append(_elide(" ".join(entry.message.split())))
    return " · ".join(parts)


def _approver_text(approver: Approver | None, language: str) -> str:
    """"display_name (username · 역할)" for an authenticated approver, else the
    "기록 없음(인증 이전)" fallback. The username is printed next to the display
    name because display names are not unique — two 김민수 on one project are a
    normal Tuesday, and an audit column that cannot tell them apart is not an
    audit column."""
    if approver is None:
        return _t(_APPROVER_NOT_RECORDED, language)
    role = _t(_ROLE_LABEL[approver.role], language)
    return _elide(f"{approver.display_name} ({approver.username} · {role})")


def _before_after_text(entry: ChangeEntry, language: str) -> str:
    # "-" means the verb carried no value param at all (every param identifies
    # an object -- e.g. set_link_behavior_type_driving_behavior, which only
    # wires existing objects together, observed in a real session ledger).
    # Rendering "기록 없음 → -" for those would read like missing data; the
    # target column already carries the whole story.
    if entry.change_type != "set" or entry.after == "-":
        return entry.after
    before = entry.before or _t(_NOT_RECORDED, language)
    return _elide(f"{before} → {entry.after}")


# ---------------------------------------------------------------------------
# DOCX rendering
# ---------------------------------------------------------------------------


def add_audit_trail_section(
    doc,
    ledger: AuditLedger,
    *,
    language: str = "ko",
    generated_at: datetime | None = None,
    table_number_start: int = 1,
) -> int:
    """Append the audit-trail section to an open python-docx ``Document`` and
    return the next free table number.

    Styling helpers are borrowed from the existing report pipeline
    (``services/report_builder.py`` / ``services/report_exports.py``) rather
    than reimplemented, so this section is visually identical to the report's
    other tables; they are imported lazily here because those modules pull the
    whole dashboard data stack (datasets/results/validation) at import time and
    an audit export must not depend on any of it.
    """
    from services.report_builder import _caption, _section_heading, _styled
    from services.report_exports import _setcell

    generated = generated_at or datetime.now(timezone.utc)
    table_no = table_number_start

    _section_heading(doc, _t(SECTION_TITLE, language))
    _styled(
        doc,
        _t(_META_LINE, language).format(
            session=ledger.session_id,
            source=ledger.source_filename or "-",
            records=ledger.record_count,
            generated=generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        ),
        "Body Text",
    )
    _styled(doc, _t(_INTRO, language), "Body Text")

    # --- Table 1: applied / rolled back / failed changes --------------------
    _caption(doc, _t(_TABLE1_CAPTION, language).format(n=table_no))
    table_no += 1
    if not ledger.changes:
        _styled(doc, _t(_EMPTY_CHANGES, language), "Body Text")
    else:
        head = _TABLE1_HEAD.get(language, _TABLE1_HEAD["ko"])
        table = doc.add_table(rows=1 + len(ledger.changes), cols=len(head))
        table.style = "Table Grid"
        for i, cell in enumerate(table.rows[0].cells):
            _setcell(cell, head[i], bold=True, fill="D9D9D9")
        for row_index, entry in enumerate(ledger.changes, start=1):
            cells = table.rows[row_index].cells
            _setcell(cells[0], entry.seq)
            _setcell(cells[1], entry.when)
            _setcell(cells[2], entry.target, align="left")
            _setcell(cells[3], entry.attribute, align="left")
            _setcell(cells[4], _before_after_text(entry, language), align="left")
            _setcell(cells[5], entry.tool, align="left")
            _setcell(cells[6], _verification_text(entry, language), align="left")
            _setcell(cells[7], _approver_text(entry.approver, language), align="left")
            _setcell(cells[8], entry.reason or _t(_NONE_MARK, language), align="left")
        doc.add_paragraph("")

    # --- Table 2: refusals, rollbacks, declines -----------------------------
    _caption(doc, _t(_TABLE2_CAPTION, language).format(n=table_no))
    table_no += 1
    if not ledger.refusals:
        _styled(doc, _t(_EMPTY_REFUSALS, language), "Body Text")
    else:
        head = _TABLE2_HEAD.get(language, _TABLE2_HEAD["ko"])
        table = doc.add_table(rows=1 + len(ledger.refusals), cols=len(head))
        table.style = "Table Grid"
        for i, cell in enumerate(table.rows[0].cells):
            _setcell(cell, head[i], bold=True, fill="D9D9D9")
        for row_index, entry in enumerate(ledger.refusals, start=1):
            cells = table.rows[row_index].cells
            phase_label = _t(_PHASE.get(entry.phase, _PHASE["dispatch"]), language)
            message = _elide(" ".join(entry.message.split())) if entry.message else _t(_NONE_MARK, language)
            _setcell(cells[0], entry.seq)
            _setcell(cells[1], entry.when)
            _setcell(cells[2], entry.tool, align="left")
            _setcell(cells[3], entry.target, align="left")
            _setcell(cells[4], phase_label)
            _setcell(cells[5], message, align="left")
            _setcell(cells[6], _approver_text(entry.approver, language), align="left")
            _setcell(cells[7], entry.reason or _t(_NONE_MARK, language), align="left")
        doc.add_paragraph("")

    other = sum(1 for e in ledger.changes if e.outcome != "applied")
    _styled(
        doc,
        _t(_SUMMARY, language).format(
            applied=ledger.applied_count,
            other=other,
            refused=len(ledger.refusals),
            reasoned=ledger.reasoned_count,
        ),
        "Body Text",
    )
    _styled(doc, _t(_FOOTNOTE, language), "Body Text")
    return table_no


def build_audit_trail_docx(
    ledger: AuditLedger, *, language: str = "ko", generated_at: datetime | None = None
) -> bytes:
    """The standalone "파라미터 변경 기록 + 근거" document the download
    endpoint streams: the section above on the report template's own styles."""
    from docx import Document
    from services.report_builder import _TEMPLATE_PATH, _apply_heading_colors

    doc = Document(_TEMPLATE_PATH) if os.path.exists(_TEMPLATE_PATH) else Document()
    add_audit_trail_section(doc, ledger, language=language, generated_at=generated_at)
    _apply_heading_colors(doc)

    out = BytesIO()
    doc.save(out)
    return out.getvalue()


__all__ = [
    "Approver",
    "AuditLedger",
    "ChangeEntry",
    "RefusalEntry",
    "SECTION_TITLE",
    "add_audit_trail_section",
    "build_audit_trail_docx",
    "build_ledger",
    "format_ts",
    "ledger_path",
    "normalize_actor",
    "read_ledger",
    "read_records",
    "resolve_session_dir",
    "unit_for",
]

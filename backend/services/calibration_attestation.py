"""Signed human attestations on §2.12.1 calibration steps (unit #53).

A NEW EVIDENCE KIND, and the only one in this product a machine did not
produce. Nine of the thirteen MRWA steps have no artifact this app can read —
six are permanent 수동 확인 영역 (②③④⑤⑧⑬), and three more (⑨⑩⑪) have an
evidence path that answers only for a network and a run that reached it. Until
this unit the honest rail said :data:`~services.calibration_rail.NO_EVIDENCE`
for all of them forever, which is true and unhelpful: the modeller who DID the
step in Vissim had nowhere to say so, and no screen could tell "nobody has done
this" from "somebody did it and this tool cannot see it".

So this store records an accountable CLAIM — who says the step is done, when
they said it, why, and against which network — and
:mod:`services.calibration_rail` renders it as its own status word
(``CONFIRMED_BY_PERSON``) that is never conflated with ``DONE``. Same doctrine
as ``genesis_jobs.ATTESTATION_BASIS`` and
``services.genesis.phase_diagram.MappingAttestation``: the app cannot prove the
claim from anything it holds, so it records whose claim it is instead of
pretending to a verification it did not perform.

WHAT IS SERVER-STAMPED, AND WHY EVERY ONE OF THEM IS
----------------------------------------------------
``attested_by`` is the SIGNED-IN USER and ``attested_at`` is the SERVER CLOCK —
neither is a field on the request model, and
:class:`routers.calibration.StepAttestationRequest` forbids extra keys, so a
body carrying either is a 422 at the door rather than a name somebody else's
session put against a step. This is verbatim the rule
``routers/genesis.confirm_phase_diagram`` already enforces for 현시도
confirmations, for verbatim its reason: a claim about who said what, dated by
the client, is not an attestation.

``reason`` is MANDATORY and non-empty after stripping. A signature with no
stated reason is a tick, and a tick is the thing this whole codebase is
arranged against.

THE STALENESS KEY IS THE ``.inpx``'s OWN BYTES — NOT ``dataset_revision``
-------------------------------------------------------------------------
An attestation is a claim about a NETWORK, so it must expire when that network
changes and only then. The obvious key, ``services.datasets`` ``revision``, is
the wrong one and the repo already carries the measurement:
``backend/tools/calibration_loop_live_check.py`` (MEASURED 2026-08-07, in the
comment above its baseline stage) records that ``dataset_revision`` folds the
OBSERVED WORKBOOK into the same string as the network —
``…|real_counts.xlsx:missing`` becomes ``…|real_counts.xlsx:<stamp>`` the moment
counts are uploaded — which retroactively invalidated runs that had completed 90
seconds earlier. Observed counts cannot change what a human confirmed about the
geometry, the speeds or the lane use, so keying on ``dataset_revision`` would
mass-expire every signature in a workspace the first time somebody uploaded a
count workbook. This module hashes the ``.inpx`` BYTES instead: it moves when
the network moves, and it does not move when anything else does.

An unreadable network yields ``None``, and ``None`` is treated as STALE, never
as fresh. Unknown is not zero — the same rule step ⑨ already applies to a
missing ``pt_line_count``.

APPEND-ONLY: A REVOCATION IS AN EVENT, NOT A DELETE
----------------------------------------------------
Any signed-in user may revoke any attestation, and revoking REQUIRES its own
reason. Nothing is removed: the record keeps ``revoked``/``revoked_by``/
``revoked_at``/``revoked_reason`` and both events stay in ``history``, so "who
signed this, and who later said it was wrong" is answerable from the file
afterwards. A revoked attestation stops being evidence immediately — it never
appears in :func:`index_payload`, which is the only thing the rail reads.

WHY ANY SIGNED-IN USER MAY REVOKE (decided 2026-08-23, recorded here). The
alternative — only the attester — makes a stale signature permanent the moment
its author leaves the project, and there is nothing to protect: revocation
only ever REMOVES a positive claim, and it is signed by whoever did it. The
asymmetry is deliberate — making a claim and withdrawing one are not the same
act, and only one of them can overstate what this product knows.

STORAGE
-------
``<workspace>/calibration/step-attestations/registry.json`` — a workspace-root
sibling of ``uploads/`` and ``observations/``, deliberately NOT under
``genesis/``: a step attestation is about the ACTIVE dataset (which may have
been uploaded by hand and never generated here), not about a generation job.
The read/write cycle is unit U6's, borrowed through ``services/observations.py``:
a module ``RLock`` held across read → mutate → write, a same-directory temp file
+ fsync + ``os.replace``, and a registry that does not parse is QUARANTINED
aside with its bytes intact rather than silently replaced.

No COM, no Vissim, no network call. Everything here runs on a laptop.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services import workspaces
from services.calibration_rail import ATTESTABLE_STEPS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

ATTESTATION_DOC_KIND = "prism.calibration_step_attestation.v1"

CALIBRATION_DIRNAME = "calibration"
ATTESTATIONS_DIRNAME = "step-attestations"
REGISTRY_FILENAME = "registry.json"

#: Same hex-only, length-capped id shape every client-supplied id in this repo
#: uses (``observations._SET_ID_RE``): a value that cannot hold a separator or
#: a ``..`` segment even if it ever reached a path.
_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

#: Bounded, because this repo has a scar about unbounded stored strings. Long
#: enough for a paragraph of justification, short enough that a registry cannot
#: be grown into a payload store.
MAX_REASON_CHARS = 2000

#: Serializes read-modify-write of the registry FILE inside this process.
#: Re-entrant for the same reason ``observations``' is: the quarantine path
#: writes a fresh registry from inside a read.
_REGISTRY_LOCK = threading.RLock()

_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 0.02

#: How many quarantine incidents a registry keeps; newest last.
_MAX_INCIDENTS = 20

#: ``str(registry path)`` -> disclosure / last good record count, keyed by path
#: so each workspace (and each test tmp root) gets its own state.
_registry_health: dict[str, dict[str, Any]] = {}
_last_known_record_count: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Errors + refusal codes
# ---------------------------------------------------------------------------


class AttestationStoreError(RuntimeError):
    """Base class for every failure this module raises."""


class RegistryUnavailableError(AttestationStoreError):
    """The registry exists but its current contents could not be read.

    Exists so a WRITE can refuse: overwriting bytes we could not read turns a
    transient I/O problem into permanent loss of somebody's signature. Read
    paths degrade to "no attestations"; write paths propagate this.
    """


class StepAttestationRefused(AttestationStoreError):
    """A signature that must not be recorded, with the reason as data.

    Same envelope as ``observations.ObservationRefusal``: ``code`` is stable
    and machine-readable, ``messages`` is the ko/en/ja triple the dashboard
    renders, and the router turns it into a 4xx whose body IS :meth:`to_dict` —
    a refusal with a reason, never a bare 400.
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
            "recorded": False,
            "reason_code": self.code,
            "message": self.messages,
            **self.detail,
        }


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


class AttestationEvent(BaseModel):
    """One thing that happened to a signature, kept forever.

    A record is created once and revoked at most once, so ``history`` holds at
    most two of these — it is a LEDGER rather than a log, which is why it is
    unbounded without being a growth risk.
    """

    model_config = ConfigDict(extra="forbid")

    event: Literal["attested", "revoked"]
    at: str = Field(min_length=1)
    by: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)


class StepAttestation(BaseModel):
    """A named human's claim that §2.12.1 step N is done on THIS network.

    Trimmed from ``services.genesis.phase_diagram.MappingAttestation``: that
    record binds a mapping to two sets of bytes and carries the mapping itself;
    this one carries no mapping at all, because there is nothing structured to
    confirm — the claim is "I did this step, here is why I say so", bound to the
    ``.inpx`` it was made against.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.calibration_step_attestation.v1"] = ATTESTATION_DOC_KIND
    id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    step_number: int
    attested_by: str = Field(
        min_length=1,
        description="The signed-in username. An attestation nobody signed is not one.",
    )
    attested_at: str = Field(min_length=1, description="The SERVER's clock, never the client's.")
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    #: The ``.inpx`` this claim was made against. See the module docstring for
    #: why this is the file's bytes and not ``datasets.dataset_revision``.
    inpx_sha256: str = Field(min_length=64, max_length=64)
    revoked: bool = False
    revoked_by: str | None = None
    revoked_at: str | None = None
    revoked_reason: str | None = None
    history: list[AttestationEvent] = Field(default_factory=list)

    @field_validator("step_number")
    @classmethod
    def _step_is_attestable(cls, value: int) -> int:
        """Only the nine steps with no machine evidence path may be signed.

        ①⑥⑦⑫ are refused BY NAME rather than accepted-and-ignored: each of
        them has an artifact that can prove or refuse it (the generation gate,
        the 현시도 attestations, ``verify_applied_row`` twice), so a signature
        there would be a human opinion sitting beside a measurement — and the
        first time the two disagreed, the screen would have to pick one.
        """
        if value not in ATTESTABLE_STEPS:
            raise ValueError(
                f"step {value} cannot be attested by hand. Signable steps are "
                f"{', '.join(str(item) for item in ATTESTABLE_STEPS)}; the others carry a "
                "machine evidence path that proves or refuses them."
            )
        return value


# ---------------------------------------------------------------------------
# Paths + the staleness key
# ---------------------------------------------------------------------------


def attestations_dir() -> Path:
    """``<current workspace>/calibration/step-attestations``.

    Derived per call from the bound workspace, like every workspace-scoped path
    since unit 1.4 — so two projects cannot see each other's signatures,
    physically rather than by filter.
    """
    return (
        workspaces.workspace_dir(workspaces.current_workspace_id())
        / CALIBRATION_DIRNAME
        / ATTESTATIONS_DIRNAME
    )


def registry_path() -> Path:
    return attestations_dir() / REGISTRY_FILENAME


def active_inpx_sha256() -> str | None:
    """SHA-256 of the ACTIVE dataset's ``.inpx`` bytes, or ``None``.

    ``None`` means "this server could not read the network", which every caller
    here treats as STALE rather than as fresh — a signature whose subject cannot
    be identified is not a signature about the network on screen.

    Read in chunks: a real consultant ``.inpx`` runs to tens of megabytes and
    this is called on every rail read.
    """
    try:
        from services.datasets import get_active_inpx_path

        path = Path(get_active_inpx_path())
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception as exc:  # noqa: BLE001 — an unreadable network is a state
        logger.info(
            "calibration attestations: the active .inpx could not be hashed (%s: %s); every "
            "signature in this workspace will read as needing re-confirmation until it can be",
            type(exc).__name__,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def record_attestation(*, step_number: int, reason: str, attested_by: str) -> dict[str, Any]:
    """Record ONE signature. Returns the stored record's summary.

    Raises:
        StepAttestationRefused: the step is not signable, the reason is empty
            or over-long, the attester could not be named, or no network could
            be hashed to bind the claim to.
    """
    signer = (attested_by or "").strip()
    if not signer:
        raise StepAttestationRefused(
            "calibration_attestation_no_attester",
            {
                "ko": "서명할 사람을 확인할 수 없습니다. 로그인한 사용자만 단계를 확인할 수 있습니다 — "
                "아무도 서명하지 않은 확인서는 확인서가 아닙니다.",
                "en": "The attester could not be identified. Only a signed-in user can confirm a "
                "step — an attestation nobody signed is not one.",
                "ja": "署名者を確認できません。ログイン中の利用者のみが段階を確認できます — "
                "誰も署名していない確認書は確認書ではありません。",
            },
            status=401,
        )

    cleaned = _clean_reason(reason)

    if step_number not in ATTESTABLE_STEPS:
        raise StepAttestationRefused(
            "calibration_attestation_step_not_attestable",
            {
                "ko": f"{step_number}단계는 사람 증언으로 확인할 수 없습니다. 서명 가능한 단계는 "
                f"{', '.join(str(item) for item in ATTESTABLE_STEPS)}번이며, 나머지 단계에는 "
                "기계가 판정할 수 있는 근거가 이미 있습니다.",
                "en": f"Step {step_number} cannot be confirmed by human attestation. The signable "
                f"steps are {', '.join(str(item) for item in ATTESTABLE_STEPS)}; the others "
                "already carry evidence a machine can judge.",
                "ja": f"段階{step_number}は人の証言では確認できません。署名可能な段階は"
                f"{', '.join(str(item) for item in ATTESTABLE_STEPS)}であり、他の段階には"
                "機械が判定できる根拠が既にあります。",
            },
            status=400,
            detail={"step_number": step_number, "attestable_steps": list(ATTESTABLE_STEPS)},
        )

    sha = active_inpx_sha256()
    if not sha:
        raise StepAttestationRefused(
            "calibration_attestation_no_network",
            {
                "ko": "확인 대상 네트워크를 읽을 수 없어 서명을 기록하지 않았습니다. 증언은 특정 "
                "네트워크에 대한 것이며, 어떤 네트워크였는지 적을 수 없으면 나중에 그 증언이 "
                "여전히 유효한지 알 수 없습니다.",
                "en": "The network being confirmed could not be read, so nothing was recorded. An "
                "attestation is about ONE network, and a claim we cannot bind to a network is a "
                "claim nobody can later check is still current.",
                "ja": "確認対象のネットワークを読み込めないため署名を記録していません。証言は特定の"
                "ネットワークについてのものであり、どのネットワークかを記録できなければ、後で"
                "その証言が有効かどうか判断できません。",
            },
            status=409,
            detail={"step_number": step_number},
        )

    now = _now_iso()
    record = StepAttestation(
        id=uuid4().hex[:12],
        workspace_id=workspaces.current_workspace_id(),
        step_number=step_number,
        attested_by=signer,
        attested_at=now,
        reason=cleaned,
        inpx_sha256=sha,
        history=[AttestationEvent(event="attested", at=now, by=signer, reason=cleaned)],
    )
    payload = record.model_dump()

    def store(records: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        records[payload["id"]] = payload
        return payload, True

    _mutate_registry(store)
    logger.info(
        "calibration attestations: %s signed step %d (%s)",
        signer,
        step_number,
        payload["id"],
    )
    return summarize(payload, current_sha=sha)


def revoke_attestation(attestation_id: str, *, revoked_by: str, reason: str) -> dict[str, Any]:
    """Withdraw ONE signature. Nothing is deleted; a second event is appended.

    Raises:
        StepAttestationRefused: unknown id, already revoked, empty reason, or an
            unnameable revoker.
    """
    revoker = (revoked_by or "").strip()
    if not revoker:
        raise StepAttestationRefused(
            "calibration_attestation_no_revoker",
            {
                "ko": "철회하는 사람을 확인할 수 없습니다. 철회도 서명된 행위입니다.",
                "en": "The person revoking could not be identified. A revocation is a signed act too.",
                "ja": "撤回する人を確認できません。撤回も署名された行為です。",
            },
            status=401,
        )
    cleaned = _clean_reason(reason)
    if not attestation_id or not _ID_RE.match(attestation_id):
        raise _not_found(attestation_id)

    now = _now_iso()
    outcome: dict[str, Any] = {}

    def mutate(records: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        current = records.get(attestation_id)
        if current is None:
            raise _not_found(attestation_id)
        if current.get("revoked") is True:
            raise StepAttestationRefused(
                "calibration_attestation_already_revoked",
                {
                    "ko": "이미 철회된 확인서입니다. 철회를 두 번 기록하면 어느 쪽이 실제 철회인지 "
                    "알 수 없게 됩니다.",
                    "en": "This attestation is already revoked. Recording a second revocation would "
                    "make it impossible to say which one actually withdrew it.",
                    "ja": "既に撤回された確認書です。撤回を二度記録すると、どちらが実際の撤回か"
                    "分からなくなります。",
                },
                status=409,
                detail={"attestation_id": attestation_id},
            )
        updated = dict(current)
        updated["revoked"] = True
        updated["revoked_by"] = revoker
        updated["revoked_at"] = now
        updated["revoked_reason"] = cleaned
        # APPEND. The attesting event stays exactly as it was written — "who
        # signed this, and who later said it was wrong" has to survive the
        # withdrawal, or a revocation would be a quiet delete with extra steps.
        history = list(updated.get("history") or [])
        history.append({"event": "revoked", "at": now, "by": revoker, "reason": cleaned})
        updated["history"] = history
        records[attestation_id] = updated
        outcome.update(updated)
        return updated, True

    _mutate_registry(mutate)
    logger.info(
        "calibration attestations: %s revoked %s (step %s)",
        revoker,
        attestation_id,
        outcome.get("step_number"),
    )
    return summarize(outcome, current_sha=active_inpx_sha256())


def _not_found(attestation_id: str) -> StepAttestationRefused:
    return StepAttestationRefused(
        "calibration_attestation_not_found",
        {
            "ko": "그런 확인서가 이 프로젝트에 없습니다.",
            "en": "No such attestation exists in this project.",
            "ja": "その確認書はこのプロジェクトにありません。",
        },
        status=404,
        detail={"attestation_id": attestation_id},
    )


def _clean_reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise StepAttestationRefused(
            "calibration_attestation_reason_required",
            {
                "ko": "이유를 적어야 합니다. 이유 없는 서명은 체크 표시일 뿐이고, 이 제품은 "
                "체크 표시를 근거로 삼지 않습니다.",
                "en": "A reason is required. A signature with no stated reason is a tick, and this "
                "product does not treat a tick as evidence.",
                "ja": "理由の記入が必要です。理由のない署名はチェック印にすぎず、本製品はチェック印を"
                "根拠としません。",
            },
        )
    if len(cleaned) > MAX_REASON_CHARS:
        raise StepAttestationRefused(
            "calibration_attestation_reason_too_long",
            {
                "ko": f"이유는 최대 {MAX_REASON_CHARS}자입니다.",
                "en": f"A reason may be at most {MAX_REASON_CHARS} characters.",
                "ja": f"理由は最大{MAX_REASON_CHARS}文字です。",
            },
            detail={"length": len(cleaned)},
        )
    return cleaned


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def summarize(record: Mapping[str, Any], *, current_sha: str | None) -> dict[str, Any]:
    """One record as the rail and the screen read it — with ``stale`` computed.

    ``stale`` is TRUE whenever the network on disk is not the one this claim was
    made against, INCLUDING when the network could not be read at all. Unknown
    is not fresh; the rail then falls the step back to its underlying derived
    status and says a signature exists but needs re-confirming.
    """
    stored_sha = str(record.get("inpx_sha256") or "")
    return {
        "id": record.get("id"),
        "step_number": record.get("step_number"),
        "attested_by": record.get("attested_by"),
        "attested_at": record.get("attested_at"),
        "reason": record.get("reason"),
        "inpx_sha256": stored_sha,
        "current_inpx_sha256": current_sha,
        "stale": not current_sha or current_sha != stored_sha,
        "revoked": bool(record.get("revoked")),
        "revoked_by": record.get("revoked_by"),
        "revoked_at": record.get("revoked_at"),
        "revoked_reason": record.get("revoked_reason"),
        "history": [dict(event) for event in (record.get("history") or []) if isinstance(event, Mapping)],
    }


def list_attestations() -> list[dict[str, Any]]:
    """Every signature in THIS workspace, newest first — revoked ones included.

    Revoked records are LISTED (this is the audit view) and never fed to the
    rail (:func:`index_payload` drops them). The two answers are deliberately
    different: history is a record, evidence is a claim that still stands.
    """
    current = active_inpx_sha256()
    return sorted(
        (summarize(record, current_sha=current) for record in _read_records().values()),
        key=lambda record: (str(record.get("attested_at") or ""), str(record.get("id") or "")),
        reverse=True,
    )


def index_payload() -> dict[int, dict[str, Any]]:
    """``{step number: the signature that STANDS}`` — the rail's evidence key.

    One per step: the newest non-revoked record. A revoked signature is not in
    here at all, which is what makes "a revoked attestation cannot upgrade a
    step" a property of the data the rail is handed rather than a branch inside
    it. (:mod:`services.calibration_rail` re-checks the flag anyway — two guards,
    because one of them being deleted must not silently restore the claim.)
    """
    current = active_inpx_sha256()
    newest: dict[int, dict[str, Any]] = {}
    for record in _read_records().values():
        if record.get("revoked") is True:
            continue
        try:
            number = int(record.get("step_number"))
        except (TypeError, ValueError):
            continue
        key = (str(record.get("attested_at") or ""), str(record.get("id") or ""))
        prior = newest.get(number)
        if prior is None or key > (str(prior.get("attested_at") or ""), str(prior.get("id") or "")):
            newest[number] = dict(record)
    return {number: summarize(record, current_sha=current) for number, record in newest.items()}


def get_attestation(attestation_id: str) -> dict[str, Any] | None:
    """One record, or ``None``. Never raises for a malformed id."""
    if not attestation_id or not _ID_RE.match(attestation_id):
        return None
    record = _read_records().get(attestation_id)
    if record is None:
        return None
    return summarize(record, current_sha=active_inpx_sha256())


# ---------------------------------------------------------------------------
# Registry I/O — unit U6's pattern, through services/observations.py
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _mutate_registry(
    mutator: Callable[[dict[str, dict[str, Any]]], tuple[_T, bool]]
) -> _T:
    """THE read-modify-write funnel. Nothing else may call :func:`_write_payload`."""
    with _REGISTRY_LOCK:
        path = registry_path()
        payload = _read_payload(path)
        result, changed = mutator(payload["attestations"])
        if changed:
            _write_payload(path, payload)
        return result


def _read_records() -> dict[str, dict[str, Any]]:
    """Records for READ paths. Never raises — an unreadable registry degrades to
    "no attestations" and the state is disclosed by :func:`registry_health_status`.

    Degrading to EMPTY is the safe direction here and only here: with no records
    the rail reports the underlying derived status, which is the pre-#53 answer.
    The opposite failure — inventing a signature — is the one that cannot be
    allowed, and this path structurally cannot produce one.
    """
    try:
        return _read_payload(registry_path())["attestations"]
    except RegistryUnavailableError as exc:
        logger.error(
            "calibration attestations: reading the registry at %s failed (%s); reporting NO "
            "attestations for this call. The file is untouched and nothing will be written over it.",
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
                path, raw, ValueError("registry payload is not an {'attestations': {...}} object")
            )
        _remember_state(path, normalized)
        return normalized


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """THE only writer: same-directory temp file -> fsync -> ``os.replace``."""
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
                    "calibration attestations: could not remove the temp registry file %s after a "
                    "failed write; it is inert (the live registry is untouched).",
                    tmp_path,
                )
            raise
        _remember_state(path, payload)


def _empty_payload() -> dict[str, Any]:
    return {"attestations": {}, "incidents": []}


def _normalize_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    records = payload.get("attestations")
    if not isinstance(records, dict):
        return None

    clean: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if not isinstance(record, dict):
            logger.warning(
                "calibration attestations: dropping unreadable registry entry %r (%s, not an object)",
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
    return {"attestations": clean, "incidents": clean_incidents[-_MAX_INCIDENTS:]}


def _quarantine_and_restart(path: Path, raw: bytes, exc: Exception) -> dict[str, Any]:
    """Move a corrupt registry aside, start a fresh one, record the loss."""
    key = str(path)
    lost = _last_known_record_count.get(key)
    try:
        quarantine_path = _quarantine_registry_file(path)
    except OSError as move_exc:
        raise RegistryUnavailableError(
            f"The calibration attestation registry at {path} did not parse "
            f"({type(exc).__name__}: {exc}) and could not be moved aside for quarantine "
            f"({type(move_exc).__name__}: {move_exc}). Refusing to overwrite it — those bytes are "
            "the only copy of this project's signatures."
        ) from move_exc

    incident = {
        "code": "registry_quarantined",
        "at": _now_iso(),
        "quarantine_path": str(quarantine_path),
        "corrupt_bytes": len(raw),
        # None means "unknown", never "zero".
        "lost_record_count": lost,
        "parse_error": f"{type(exc).__name__}: {exc}",
    }
    logger.error(
        "calibration attestations: the registry at %s is CORRUPT (%s: %s, %d bytes). Quarantined "
        "at %s and a fresh registry started. Signatures lost: %s. Nothing was deleted.",
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
    _last_known_record_count[key] = len(payload.get("attestations") or {})
    incidents = [
        entry
        for entry in (payload.get("incidents") or [])
        if isinstance(entry, dict) and entry.get("code") == "registry_quarantined"
    ]
    if not incidents:
        _registry_health.pop(key, None)
        return
    latest = incidents[-1]
    lost = latest.get("lost_record_count")
    lost = int(lost) if isinstance(lost, int) else None
    quarantine_path = latest.get("quarantine_path")
    if lost is None:
        count_ko = "몇 건이 사라졌는지는 알 수 없습니다"
        count_en = "how many signatures were lost is unknown"
        count_ja = "何件が失われたかは不明です"
    else:
        count_ko = f"확인서 {lost}건을 잃었습니다"
        count_en = f"{lost} attestation(s) were lost"
        count_ja = f"確認書{lost}件が失われました"
    _registry_health[key] = {
        "code": "registry_quarantined",
        "at": latest.get("at"),
        "quarantine_path": quarantine_path,
        "lost_record_count": lost,
        "quarantine_count": len(incidents),
        "messages": {
            "ko": (
                f"단계 확인서 목록 파일이 손상되어 격리했습니다. {count_ko}. 손상된 원본은 "
                f"{quarantine_path} 에 그대로 보관되어 있습니다."
            ),
            "en": (
                f"The step-attestation registry file was corrupt and has been quarantined; "
                f"{count_en}. The original bytes are preserved at {quarantine_path}."
            ),
            "ja": (
                f"段階確認書の一覧ファイルが破損していたため隔離しました。{count_ja}。破損した原本は "
                f"{quarantine_path} にそのまま保存されています。"
            ),
        },
    }


def registry_health_status() -> dict[str, Any] | None:
    """The current workspace's quarantine disclosure, or ``None`` when healthy."""
    return _registry_health.get(str(registry_path()))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "ATTESTATION_DOC_KIND",
    "MAX_REASON_CHARS",
    "AttestationEvent",
    "AttestationStoreError",
    "RegistryUnavailableError",
    "StepAttestation",
    "StepAttestationRefused",
    "active_inpx_sha256",
    "attestations_dir",
    "get_attestation",
    "index_payload",
    "list_attestations",
    "record_attestation",
    "registry_health_status",
    "registry_path",
    "revoke_attestation",
    "summarize",
]

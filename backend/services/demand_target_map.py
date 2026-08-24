"""Which vehicle input drives a node's total — NAMED BY A PERSON (unit #54).

THE GAP THIS CLOSES (measured 2026-08-24, the user's own screen)
----------------------------------------------------------------
``services.agent.reality_check_targets`` derives a node's demand target only
where the join is an IDENTITY: exactly one vehicle input feeds a link the node
was counted entering from. On the real client model node 8 has SIX candidates
over TWO approach links (109/209/309 and 113/213/313), so the derivation refuses
by name — ``MULTIPLE_VEHICLE_INPUT_CANDIDATES`` — the report comes back
``NO_TARGET_OBJECT``, the ladder cannot establish whether the entrance volume is
measured data, and the node reads 분류 불가 / ``NO_DEMAND_TARGET`` forever.

Meanwhile ``backend/tools/calibration_loop_live_check.py`` reaches
``CAPACITY_LIMIT`` on the same node — because a HUMAN did the graph analysis
once (link 617 carries 71.9% of everything that can reach node 8; input 113
alone carries 69.0%) and hardcoded ``{node 8 -> input 113, link 617}`` into the
harness. That knowledge exists, it is a person's, and until this unit there was
nowhere in the product to put it. A refusal nobody can answer is not a refusal,
it is a dead end.

So this store records the answer as what it is: an accountable CLAIM. Same
species as ``services.calibration_attestation`` (#53) and
``services.genesis.phase_diagram.MappingAttestation`` — who says so, when, why,
and against which network — and the same doctrine, stated once more because it
is the whole reason this module may exist: the app cannot PROVE that input 113
drives node 8's total, so it records whose claim it is instead of pretending to
a verification it did not perform. The resulting proposal still carries
``fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL``. A signer does not make the assumption
proven; a signer makes it ATTRIBUTED.

WHAT A NAMED MAPPING MUST BE, OR IT IS REFUSED
-----------------------------------------------
**REAL at naming time.** The (node, input, link) triple has to be one the
derivation itself computed as a candidate for that node — i.e. the input exists
in the shadow model and its link is among the node's counted approach links.
The candidate list is passed IN (``routers/calibration`` reads it from
``reality_check_targets.derive_targets``'s own per-node output) rather than
recomputed here, because a second opinion about which inputs feed a node is
exactly the divergence this store must not introduce. A triple that is not on
that list is refused BY NAME: laundering an unverifiable mapping into a
first-class demand target is the one thing this module could do that would be
worse than the dead end it replaces.

**SERVER-STAMPED.** ``named_by`` is the SIGNED-IN USER and ``named_at`` is the
SERVER CLOCK; neither is a field on the request model, and
``routers.calibration.DemandTargetRequest`` forbids extra keys, so a body
carrying either is a 422 at the door. Verbatim the rule #53 and
``routers/genesis.confirm_phase_diagram`` already enforce, for verbatim its
reason: a claim about who said what, dated by the client, is not a claim.

**REASONED.** ``reason`` is mandatory and non-empty after stripping. A mapping
with no stated reason is a tick, and a tick is what this codebase is arranged
against.

**BOUND TO THE NETWORK'S OWN BYTES.** ``inpx_sha256``, for #53's measured
reason: ``datasets.dataset_revision`` folds the observed workbook into the same
string (``tools/calibration_loop_live_check.py``, MEASURED 2026-08-07), so
keying staleness on it would expire every mapping in a project the first time
somebody uploaded counts. A mapping is a claim about a NETWORK's topology — it
must expire when the network moves and only then. An unreadable network yields
``None`` and ``None`` is STALE, never fresh: unknown is not zero.

A stale mapping is IGNORED by the derivation (the node falls back to the honest
absence state it had before) and still SHOWN on the screen, so the modeller can
see the claim they are being asked to re-make. That split is deliberate: a
silent disappearance would look like the mapping was never recorded.

APPEND-ONLY: A REVOCATION IS AN EVENT, NOT A DELETE
----------------------------------------------------
Any signed-in user may revoke any mapping, and revoking REQUIRES its own reason.
Nothing is removed: ``revoked``/``revoked_by``/``revoked_at``/``revoked_reason``
stay on the record and both events stay in ``history``. #53's recorded reason
for the asymmetry applies unchanged — revocation only ever REMOVES a positive
claim, so restricting it to the author would make a wrong mapping permanent the
moment its author left the project.

THE NODE'S LATEST WORD WINS, AND A WITHDRAWAL IS A WORD
--------------------------------------------------------
MEASURED by the refuter, 2026-08-24, and it was a real hole: selecting "the
newest UNREVOKED record for this node" resurrects a superseded claim. Name A,
change your mind and name B, then withdraw B — and A, which nobody has
re-affirmed since B replaced it, silently becomes the node's demand target
again. That is the opposite of what withdrawing B means, and this store's own
promise ("the node falls back to whatever the network itself supports") was
false on exactly that path.

So :func:`_latest_by_node` picks the newest record for a node REGARDLESS of its
revoked flag, and :func:`index_payload` then drops it if that record is a
withdrawal. Nothing stands. Getting A back is not something the store does on
somebody's behalf: it is a NEW record, with fresh server stamps and a fresh
reason, which is the only form in which this product accepts a claim at all.

**Newest is a SEQUENCE, not a clock.** The obvious key — ``named_at`` with the
record id as a tie-break — was measured wrong the same day: ``_now_iso``
truncates to seconds, so two records written inside one second tie, and the
tie-break then compares two random ``uuid4`` fragments. ``"zzzz…"`` beat
``"aaaa…"`` regardless of which was appended first. ``seq`` is a registry-level
integer counter, incremented under the write lock at append time, so the order
is total and IS insertion order. ``named_at`` stays on the record as display
provenance and decides nothing.

STORAGE
-------
``<workspace>/calibration/demand-targets/registry.json`` — a workspace-root
sibling of ``calibration/step-attestations/``, same family, same reason: a
demand-target mapping is about the ACTIVE dataset (which may have been uploaded
by hand and never generated here), not about a generation job. The read/write
cycle is unit U6's: a module ``RLock`` held across read → mutate → write, a
same-directory temp file + fsync + ``os.replace``, and a registry that does not
parse is QUARANTINED aside with its bytes intact rather than silently replaced.

No COM, no Vissim, no network call.
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
from typing import Any, Callable, Literal, Mapping, Sequence, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from services import workspaces

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

DEMAND_TARGET_DOC_KIND = "prism.calibration_demand_target_map.v1"

CALIBRATION_DIRNAME = "calibration"
DEMAND_TARGETS_DIRNAME = "demand-targets"
REGISTRY_FILENAME = "registry.json"

#: The same hex-only, length-capped id shape every client-supplied id in this
#: repo uses: a value that cannot hold a separator or a ``..`` segment even if
#: it ever reached a path.
_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

#: Bounded, because this repo has a scar about unbounded stored strings.
MAX_REASON_CHARS = 2000

#: How long a node id / input number / link number may be. These are Vissim
#: object numbers and workbook node ids, not free text.
MAX_TOKEN_CHARS = 64

_REGISTRY_LOCK = threading.RLock()

_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 0.02

_MAX_INCIDENTS = 20

_registry_health: dict[str, dict[str, Any]] = {}
_last_known_record_count: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Errors + refusal codes
# ---------------------------------------------------------------------------


class DemandTargetStoreError(RuntimeError):
    """Base class for every failure this module raises."""


class RegistryUnavailableError(DemandTargetStoreError):
    """The registry exists but its current contents could not be read.

    Exists so a WRITE can refuse: overwriting bytes we could not read turns a
    transient I/O problem into permanent loss of somebody's mapping. Read paths
    degrade to "no mappings"; write paths propagate this.
    """


class DemandTargetRefused(DemandTargetStoreError):
    """A mapping that must not be recorded, with the reason as data.

    Same envelope as ``calibration_attestation.StepAttestationRefused``:
    ``code`` is stable and machine-readable, ``messages`` is the ko/en/ja triple
    the dashboard renders, and the router turns it into a 4xx whose body IS
    :meth:`to_dict` — a refusal with a reason, never a bare 400.
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


class MappingEvent(BaseModel):
    """One thing that happened to a mapping, kept forever.

    A record is created once and revoked at most once, so ``history`` holds at
    most two of these — a LEDGER rather than a log, which is why it is
    unbounded without being a growth risk.
    """

    model_config = ConfigDict(extra="forbid")

    event: Literal["named", "revoked"]
    at: str = Field(min_length=1)
    by: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)


class DemandTargetMapping(BaseModel):
    """A named human's claim that vehicle input N drives node M's total.

    The three network tokens are stored as STRINGS in the same canonical form
    the derivation emits them in (``fixes.canonical_link_id`` for the link), so
    ``"617.0"`` and ``"00617"`` cannot read as a different link from ``"617"`` —
    a mismatch there does not merely lose the mapping, it would silently make a
    recorded mapping un-matchable against the very candidate list it was checked
    against.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.calibration_demand_target_map.v1"] = DEMAND_TARGET_DOC_KIND
    id: str = Field(min_length=1)
    #: THE ORDER. A registry-level counter incremented under the write lock at
    #: append time, so it is total and it IS insertion order. Everything about
    #: "which of this node's records is its latest word" is decided by this and
    #: by nothing else — see the module docstring for the two measured reasons
    #: ``named_at`` cannot be that key.
    seq: int = Field(ge=1)
    workspace_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1, max_length=MAX_TOKEN_CHARS)
    vehicle_input_no: str = Field(min_length=1, max_length=MAX_TOKEN_CHARS)
    link_no: str = Field(min_length=1, max_length=MAX_TOKEN_CHARS)
    named_by: str = Field(
        min_length=1,
        description="The signed-in username. A mapping nobody named is not one.",
    )
    named_at: str = Field(min_length=1, description="The SERVER's clock, never the client's.")
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    #: The ``.inpx`` this claim was made against. See the module docstring for
    #: why this is the file's bytes and not ``datasets.dataset_revision``.
    inpx_sha256: str = Field(min_length=64, max_length=64)
    revoked: bool = False
    revoked_by: str | None = None
    revoked_at: str | None = None
    revoked_reason: str | None = None
    history: list[MappingEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Paths + the staleness key
# ---------------------------------------------------------------------------


def demand_targets_dir() -> Path:
    """``<current workspace>/calibration/demand-targets``.

    Derived per call from the bound workspace, like every workspace-scoped path
    since unit 1.4 — so two projects cannot see each other's mappings,
    physically rather than by filter.
    """
    return (
        workspaces.workspace_dir(workspaces.current_workspace_id())
        / CALIBRATION_DIRNAME
        / DEMAND_TARGETS_DIRNAME
    )


def registry_path() -> Path:
    return demand_targets_dir() / REGISTRY_FILENAME


def active_inpx_sha256() -> str | None:
    """SHA-256 of the ACTIVE dataset's ``.inpx`` bytes, or ``None``.

    ``None`` means "this server could not read the network", which every caller
    here treats as STALE rather than as fresh — a mapping whose subject cannot
    be identified is not a mapping about the network on screen.

    Read in chunks: a real consultant ``.inpx`` runs to tens of megabytes.
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
            "demand targets: the active .inpx could not be hashed (%s: %s); every mapping in this "
            "workspace will read as needing re-confirmation until it can be",
            type(exc).__name__,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# The candidate check — the one rule that keeps this from being a launderer
# ---------------------------------------------------------------------------


def candidate_key(entry: Mapping[str, Any]) -> tuple[str, str] | None:
    """``(input no, canonical link)`` for one derivation candidate, or ``None``.

    The link goes through ``fixes.canonical_link_id`` — the SAME normalizer the
    derivation used to build the candidate in the first place — imported lazily
    for the reason every module in this layer defers its agent imports (see
    ``routers/assistant.py``'s note): pulling the agent package in at import
    time reorders tool registration.
    """
    from services.agent import fixes

    input_no = str(entry.get("vehicle_input_no") or entry.get("no") or "").strip()
    link_raw = entry.get("link_no", entry.get("link"))
    link_no = fixes.canonical_link_id(link_raw) if link_raw not in (None, "") else ""
    if not input_no or not link_no:
        return None
    return input_no, str(link_no)


def _canonical_link(value: Any) -> str:
    from services.agent import fixes

    if value in (None, ""):
        return ""
    return str(fixes.canonical_link_id(value) or "")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def record_mapping(
    *,
    node_id: str,
    vehicle_input_no: str,
    link_no: str,
    reason: str,
    named_by: str,
    candidates: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Record ONE named mapping. Returns the stored record's summary.

    ``candidates`` is the derivation's own per-node candidate list — the inputs
    whose link is among that node's counted approach links. ``None`` means the
    server could not compute one at all (no network, no workbook, no
    object-id-keyed rows), which is a refusal rather than a licence.

    Raises:
        DemandTargetRefused: nobody to name it, no reason, a malformed triple, a
            triple no candidate list vouches for, or no network to bind to.
    """
    namer = (named_by or "").strip()
    if not namer:
        raise DemandTargetRefused(
            "demand_target_no_namer",
            {
                "ko": "지정하는 사람을 확인할 수 없습니다. 로그인한 사용자만 수요 대상을 지정할 수 "
                "있습니다 — 아무도 이름을 걸지 않은 지정은 가정을 세탁하는 것일 뿐입니다.",
                "en": "The person naming this could not be identified. Only a signed-in user may "
                "name a demand target — an unsigned mapping only launders an assumption.",
                "ja": "指定する人を確認できません。ログイン中の利用者のみが需要対象を指定できます — "
                "誰も名前を出していない指定は仮定を洗浄するだけです。",
            },
            status=401,
        )

    cleaned = _clean_reason(reason)
    node = _clean_token(node_id, "node_id")
    input_no = _clean_token(vehicle_input_no, "vehicle_input_no")
    link = _canonical_link(_clean_token(link_no, "link_no"))
    if not link:
        raise _incomplete_triple("link_no")

    _require_real_triple(node, input_no, link, candidates)

    sha = active_inpx_sha256()
    if not sha:
        raise DemandTargetRefused(
            "demand_target_no_network",
            {
                "ko": "대상 네트워크를 읽을 수 없어 지정을 기록하지 않았습니다. 이 지정은 특정 "
                "네트워크의 구조에 대한 주장이며, 어떤 네트워크였는지 적을 수 없으면 나중에 그 "
                "주장이 여전히 유효한지 알 수 없습니다.",
                "en": "The network this names an input in could not be read, so nothing was "
                "recorded. A demand-target mapping is a claim about ONE network's topology, and a "
                "claim we cannot bind to a network is one nobody can later check is still current.",
                "ja": "対象ネットワークを読み込めないため指定を記録していません。この指定は特定の"
                "ネットワークの構造に関する主張であり、どのネットワークかを記録できなければ、"
                "後でその主張が有効か判断できません。",
            },
            status=409,
            detail={"node_id": node},
        )

    now = _now_iso()
    stored: dict[str, Any] = {}

    def store(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        # THE COUNTER IS ALLOCATED HERE, inside the held lock, so the number a
        # record carries is the position it was actually appended at. Doing it
        # before the lock would hand two concurrent writers the same seq and
        # make "the node's latest word" a coin toss between them.
        seq = _next_sequence(payload)
        payload["sequence"] = seq
        record = DemandTargetMapping(
            id=uuid4().hex[:12],
            seq=seq,
            workspace_id=workspaces.current_workspace_id(),
            node_id=node,
            vehicle_input_no=input_no,
            link_no=link,
            named_by=namer,
            named_at=now,
            reason=cleaned,
            inpx_sha256=sha,
            history=[MappingEvent(event="named", at=now, by=namer, reason=cleaned)],
        ).model_dump()
        payload["mappings"][record["id"]] = record
        stored.update(record)
        return record, True

    _mutate_registry(store)
    logger.info(
        "demand targets: %s named vehicle input %s (link %s) for node %s (%s, seq %s)",
        namer,
        input_no,
        link,
        node,
        stored["id"],
        stored["seq"],
    )
    return summarize(stored, current_sha=sha)


def revoke_mapping(mapping_id: str, *, revoked_by: str, reason: str) -> dict[str, Any]:
    """Withdraw ONE mapping. Nothing is deleted; a second event is appended.

    A revocation does NOT allocate a new ``seq``: the record keeps the position
    it was appended at. That is what makes withdrawing a SUPERSEDED record a
    no-op for the node (its latest word is still the later record) while
    withdrawing the LATEST record leaves the node with nothing standing — the
    two cases this store has to tell apart, and the reason
    :func:`_latest_by_node` looks at the newest record rather than at the newest
    surviving one.

    Raises:
        DemandTargetRefused: unknown id, already revoked, empty reason, or an
            unnameable revoker.
    """
    revoker = (revoked_by or "").strip()
    if not revoker:
        raise DemandTargetRefused(
            "demand_target_no_revoker",
            {
                "ko": "철회하는 사람을 확인할 수 없습니다. 철회도 서명된 행위입니다.",
                "en": "The person revoking could not be identified. A revocation is a signed act too.",
                "ja": "撤回する人を確認できません。撤回も署名された行為です。",
            },
            status=401,
        )
    cleaned = _clean_reason(reason)
    if not mapping_id or not _ID_RE.match(mapping_id):
        raise _not_found(mapping_id)

    now = _now_iso()
    outcome: dict[str, Any] = {}

    def mutate(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        records = payload["mappings"]
        current = records.get(mapping_id)
        if current is None:
            raise _not_found(mapping_id)
        if current.get("revoked") is True:
            raise DemandTargetRefused(
                "demand_target_already_revoked",
                {
                    "ko": "이미 철회된 지정입니다. 철회를 두 번 기록하면 어느 쪽이 실제 철회인지 "
                    "알 수 없게 됩니다.",
                    "en": "This mapping is already revoked. Recording a second revocation would "
                    "make it impossible to say which one actually withdrew it.",
                    "ja": "既に撤回された指定です。撤回を二度記録すると、どちらが実際の撤回か"
                    "分からなくなります。",
                },
                status=409,
                detail={"mapping_id": mapping_id},
            )
        updated = dict(current)
        updated["revoked"] = True
        updated["revoked_by"] = revoker
        updated["revoked_at"] = now
        updated["revoked_reason"] = cleaned
        # APPEND. The naming event stays exactly as it was written — "who named
        # this, and who later said it was wrong" has to survive the withdrawal,
        # or a revocation would be a quiet delete with extra steps.
        history = list(updated.get("history") or [])
        history.append({"event": "revoked", "at": now, "by": revoker, "reason": cleaned})
        updated["history"] = history
        records[mapping_id] = updated
        outcome.update(updated)
        return updated, True

    _mutate_registry(mutate)
    logger.info(
        "demand targets: %s revoked %s (node %s)", revoker, mapping_id, outcome.get("node_id")
    )
    return summarize(outcome, current_sha=active_inpx_sha256())


def _require_real_triple(
    node_id: str,
    input_no: str,
    link_no: str,
    candidates: Sequence[Mapping[str, Any]] | None,
) -> None:
    """THE rule that keeps this store from laundering an unverifiable claim.

    A person may choose BETWEEN the inputs that really feed this node's counted
    approaches. They may not invent one: an input that is not in the shadow
    model, or one whose link this node was never counted entering from, is a
    mapping nothing could ever check — and a demand target derived from it would
    be indistinguishable, downstream, from one the network itself vouched for.
    """
    if candidates is None:
        raise DemandTargetRefused(
            "demand_target_no_candidates",
            {
                "ko": f"노드 {node_id}의 차량 인풋 후보를 계산할 수 없어 지정을 기록하지 않았습니다. "
                "이 노드의 실측 행이 Vissim 이동류 ID가 아니거나, 네트워크·실측 자료를 읽지 못한 "
                "상태입니다 — 확인할 수 없는 지정은 기록하지 않습니다.",
                "en": f"No vehicle-input candidate list could be computed for node {node_id}, so "
                "nothing was recorded. Either this node's observed rows are not Vissim movement "
                "OBJECT_IDs, or the network/observed data could not be read — a mapping nothing can "
                "check is not recorded.",
                "ja": f"ノード {node_id} の車両インプット候補を計算できないため指定を記録していません。"
                "このノードの実測行がVissimの移動ID でないか、ネットワーク・実測データを読み込めない"
                "状態です — 確認できない指定は記録しません。",
            },
            status=409,
            detail={"node_id": node_id},
        )

    keys = {key for key in (candidate_key(entry) for entry in candidates) if key is not None}
    if (input_no, link_no) in keys:
        return
    raise DemandTargetRefused(
        "demand_target_unverifiable_triple",
        {
            "ko": f"차량 인풋 {input_no}(링크 {link_no})은(는) 노드 {node_id}가 실측된 진입 링크에 "
            "붙어 있는 인풋이 아닙니다. 이 제품은 실제 후보들 중에서 사람이 고르는 것만 기록합니다 — "
            "확인할 수 없는 대응은 기록해도 근거가 되지 않습니다.",
            "en": f"Vehicle input {input_no} (link {link_no}) is not one of the inputs feeding an "
            f"approach node {node_id} was counted entering from. This product records a human's "
            "CHOICE among the real candidates; a mapping nothing can verify would not become "
            "evidence by being written down.",
            "ja": f"車両インプット {input_no}(リンク {link_no})は、ノード {node_id} が実測された流入"
            "リンクに接続するインプットではありません。本製品は実在する候補の中から人が選んだ結果"
            "のみを記録します — 確認できない対応は記録しても根拠になりません。",
        },
        status=422,
        detail={
            "node_id": node_id,
            "vehicle_input_no": input_no,
            "link_no": link_no,
            "candidates": [
                {"vehicle_input_no": key[0], "link_no": key[1]} for key in sorted(keys)
            ],
        },
    )


def _not_found(mapping_id: str) -> DemandTargetRefused:
    return DemandTargetRefused(
        "demand_target_not_found",
        {
            "ko": "그런 수요 대상 지정이 이 프로젝트에 없습니다.",
            "en": "No such demand-target mapping exists in this project.",
            "ja": "その需要対象の指定はこのプロジェクトにありません。",
        },
        status=404,
        detail={"mapping_id": mapping_id},
    )


def _incomplete_triple(field: str) -> DemandTargetRefused:
    return DemandTargetRefused(
        "demand_target_incomplete_triple",
        {
            "ko": f"노드·차량 인풋·링크 세 값이 모두 필요합니다 ({field} 없음). 셋 중 하나라도 없으면 "
            "무엇을 무엇에 대응시켰는지 나중에 확인할 수 없습니다.",
            "en": f"A node, a vehicle input AND a link are all required ({field} is missing). "
            "Without all three, what was mapped to what cannot be checked later.",
            "ja": f"ノード・車両インプット・リンクの三つすべてが必要です({field} が欠けています)。"
            "一つでも欠けると、何を何に対応させたのか後から確認できません。",
        },
        detail={"field": field},
    )


def _clean_token(value: Any, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise _incomplete_triple(field)
    if len(cleaned) > MAX_TOKEN_CHARS:
        raise DemandTargetRefused(
            "demand_target_token_too_long",
            {
                "ko": f"{field} 값이 최대 {MAX_TOKEN_CHARS}자를 넘습니다.",
                "en": f"{field} may be at most {MAX_TOKEN_CHARS} characters.",
                "ja": f"{field} は最大{MAX_TOKEN_CHARS}文字です。",
            },
            detail={"field": field, "length": len(cleaned)},
        )
    return cleaned


def _clean_reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise DemandTargetRefused(
            "demand_target_reason_required",
            {
                "ko": "이유를 적어야 합니다. 이유 없는 지정은 체크 표시일 뿐이고, 이 제품은 체크 "
                "표시를 근거로 삼지 않습니다.",
                "en": "A reason is required. A mapping with no stated reason is a tick, and this "
                "product does not treat a tick as evidence.",
                "ja": "理由の記入が必要です。理由のない指定はチェック印にすぎず、本製品はチェック印を"
                "根拠としません。",
            },
        )
    if len(cleaned) > MAX_REASON_CHARS:
        raise DemandTargetRefused(
            "demand_target_reason_too_long",
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
    """One record as the derivation and the screen read it — ``stale`` computed.

    ``stale`` is TRUE whenever the network on disk is not the one this mapping
    was named against, INCLUDING when the network could not be read at all.
    Unknown is not fresh; the derivation then ignores the mapping and the node
    falls back to the absence state it had before anybody named anything.
    """
    stored_sha = str(record.get("inpx_sha256") or "")
    return {
        "id": record.get("id"),
        # The append position, on the wire because it is what "this node's
        # latest word" MEANS. A screen never compares it; a bug report quotes it.
        "seq": _sequence_of(record),
        "node_id": record.get("node_id"),
        "vehicle_input_no": record.get("vehicle_input_no"),
        "link_no": record.get("link_no"),
        "named_by": record.get("named_by"),
        "named_at": record.get("named_at"),
        "reason": record.get("reason"),
        "inpx_sha256": stored_sha,
        "current_inpx_sha256": current_sha,
        "stale": not current_sha or current_sha != stored_sha,
        "revoked": bool(record.get("revoked")),
        "revoked_by": record.get("revoked_by"),
        "revoked_at": record.get("revoked_at"),
        "revoked_reason": record.get("revoked_reason"),
        "history": [
            dict(event) for event in (record.get("history") or []) if isinstance(event, Mapping)
        ],
    }


def _sequence_of(record: Mapping[str, Any]) -> int:
    """This record's append position, or ``0`` for one that carries none.

    ``0`` rather than a raise: a hand-edited or half-written registry entry must
    not break a read a modeller is waiting on — and it must not sort ABOVE
    properly numbered records either. 0 puts it below every real one, which is
    the direction that cannot resurrect a claim.
    """
    value = record.get("seq")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 0
    return value


def _next_sequence(payload: Mapping[str, Any]) -> int:
    """The next append position, from the counter AND from the records.

    ``max`` of the two rather than the counter alone, so a registry whose
    ``sequence`` key was lost or hand-edited downwards cannot mint a number that
    already exists — a duplicate seq would make two records tie on the one key
    that has to be total.
    """
    stored = payload.get("sequence")
    counter = stored if isinstance(stored, int) and not isinstance(stored, bool) else 0
    highest = max(
        (
            _sequence_of(record)
            for record in (payload.get("mappings") or {}).values()
            if isinstance(record, Mapping)
        ),
        default=0,
    )
    return max(counter, highest, 0) + 1


def _latest_by_node(records: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{node id: that node's LATEST WORD}`` — revoked records INCLUDED.

    THE ONE SELECTION FUNCTION. Every caller that asks "what does this node say"
    goes through it, because two selections is exactly how the screen and the
    derivation would come to disagree about whether a mapping stands.

    Revoked records are candidates HERE and are dropped by :func:`index_payload`
    afterwards. That ordering is the whole fix for the resurrection hole this
    module's docstring records: a withdrawal is the node's latest word when it is
    the latest record, and a superseded record is never promoted by the
    withdrawal of the record that superseded it.
    """
    newest: dict[str, dict[str, Any]] = {}
    for record in records.values():
        if not isinstance(record, Mapping):
            continue
        node_id = str(record.get("node_id") or "")
        if not node_id:
            continue
        prior = newest.get(node_id)
        if prior is None or _sequence_of(record) > _sequence_of(prior):
            newest[node_id] = dict(record)
    return newest


def list_mappings() -> list[dict[str, Any]]:
    """Every mapping in THIS workspace, newest first — revoked ones included.

    Revoked records are LISTED (this is the audit view) and never fed to the
    derivation (:func:`index_payload` drops them). History is a record; evidence
    is a claim that still stands.

    Newest by ``seq``, the same total order every other read here uses, so the
    audit view and the standing claim cannot present two records in opposite
    orders.
    """
    current = active_inpx_sha256()
    return sorted(
        (summarize(record, current_sha=current) for record in _read_records().values()),
        key=_sequence_of,
        reverse=True,
    )


def index_payload() -> dict[str, dict[str, Any]]:
    """``{node id: the mapping that STANDS}`` — the node's latest word, if positive.

    A node whose newest record is a REVOCATION is absent from here entirely, and
    that is the fix for the measured resurrection hole (module docstring):
    "newest UNREVOKED record" hands the node back a claim that a later,
    now-withdrawn one had already replaced. Whoever wants the older mapping back
    re-names it, which is a new record with fresh server stamps.

    STALE records ARE here, carrying ``stale: true``: the screen has to show the
    claim it is asking the modeller to re-make, and the derivation checks the
    flag itself (two guards, because one of them being deleted must not silently
    restore the claim).
    """
    current = active_inpx_sha256()
    return {
        node: summarize(record, current_sha=current)
        for node, record in _latest_by_node(_read_records()).items()
        if record.get("revoked") is not True
    }


def fresh_index_payload() -> dict[str, dict[str, Any]]:
    """:func:`index_payload` with the stale ones dropped — the derivation's input.

    A filter over the SAME selection, never a second one: the router threads
    this, and the derivation re-checks ``stale``/``revoked`` anyway. Neither
    guard alone is trusted.
    """
    return {
        node: record for node, record in index_payload().items() if not record.get("stale")
    }


def get_mapping(mapping_id: str) -> dict[str, Any] | None:
    """One record, or ``None``. Never raises for a malformed id."""
    if not mapping_id or not _ID_RE.match(mapping_id):
        return None
    record = _read_records().get(mapping_id)
    if record is None:
        return None
    return summarize(record, current_sha=active_inpx_sha256())


# ---------------------------------------------------------------------------
# Registry I/O — unit U6's pattern, as #53 borrowed it
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _mutate_registry(mutator: Callable[[dict[str, Any]], tuple[_T, bool]]) -> _T:
    """THE read-modify-write funnel. Nothing else may call :func:`_write_payload`.

    The mutator is handed the WHOLE payload rather than only ``mappings``,
    because the ``sequence`` counter has to be read and bumped inside the same
    held lock as the append it numbers. A counter allocated anywhere else is a
    counter two concurrent writers can be handed twice.
    """
    with _REGISTRY_LOCK:
        path = registry_path()
        payload = _read_payload(path)
        result, changed = mutator(payload)
        if changed:
            _write_payload(path, payload)
        return result


def _read_records() -> dict[str, dict[str, Any]]:
    """Records for READ paths. Never raises — an unreadable registry degrades to
    "no mappings" and the state is disclosed by :func:`registry_health_status`.

    Degrading to EMPTY is the safe direction here and only here: with no records
    the derivation reports the honest absence state, which is the pre-#54
    answer. The opposite failure — inventing a mapping nobody made — is the one
    that cannot be allowed, and this path structurally cannot produce one.
    """
    try:
        return _read_payload(registry_path())["mappings"]
    except RegistryUnavailableError as exc:
        logger.error(
            "demand targets: reading the registry at %s failed (%s); reporting NO mappings for "
            "this call. The file is untouched and nothing will be written over it.",
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
                path, raw, ValueError("registry payload is not a {'mappings': {...}} object")
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
                    "demand targets: could not remove the temp registry file %s after a failed "
                    "write; it is inert (the live registry is untouched).",
                    tmp_path,
                )
            raise
        _remember_state(path, payload)


def _empty_payload() -> dict[str, Any]:
    return {"mappings": {}, "incidents": [], "sequence": 0}


def _normalize_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    records = payload.get("mappings")
    if not isinstance(records, dict):
        return None

    clean: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if not isinstance(record, dict):
            logger.warning(
                "demand targets: dropping unreadable registry entry %r (%s, not an object)",
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
    # The counter is carried through as-is and REPAIRED UPWARDS only. A registry
    # whose ``sequence`` was lost still allocates above every record it holds
    # (:func:`_next_sequence` takes the max), so the number can never be reissued;
    # normalizing it downwards here is the one thing that could.
    sequence = payload.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        sequence = 0
    return {
        "mappings": clean,
        "incidents": clean_incidents[-_MAX_INCIDENTS:],
        "sequence": sequence,
    }


def _quarantine_and_restart(path: Path, raw: bytes, exc: Exception) -> dict[str, Any]:
    """Move a corrupt registry aside, start a fresh one, record the loss."""
    key = str(path)
    lost = _last_known_record_count.get(key)
    try:
        quarantine_path = _quarantine_registry_file(path)
    except OSError as move_exc:
        raise RegistryUnavailableError(
            f"The demand-target registry at {path} did not parse "
            f"({type(exc).__name__}: {exc}) and could not be moved aside for quarantine "
            f"({type(move_exc).__name__}: {move_exc}). Refusing to overwrite it — those bytes are "
            "the only copy of this project's mappings."
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
        "demand targets: the registry at %s is CORRUPT (%s: %s, %d bytes). Quarantined at %s and a "
        "fresh registry started. Mappings lost: %s. Nothing was deleted.",
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
    _last_known_record_count[key] = len(payload.get("mappings") or {})
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
        count_en = "how many mappings were lost is unknown"
        count_ja = "何件が失われたかは不明です"
    else:
        count_ko = f"지정 {lost}건을 잃었습니다"
        count_en = f"{lost} mapping(s) were lost"
        count_ja = f"指定{lost}件が失われました"
    _registry_health[key] = {
        "code": "registry_quarantined",
        "at": latest.get("at"),
        "quarantine_path": quarantine_path,
        "lost_record_count": lost,
        "quarantine_count": len(incidents),
        "messages": {
            "ko": (
                f"수요 대상 지정 목록 파일이 손상되어 격리했습니다. {count_ko}. 손상된 원본은 "
                f"{quarantine_path} 에 그대로 보관되어 있습니다."
            ),
            "en": (
                f"The demand-target registry file was corrupt and has been quarantined; "
                f"{count_en}. The original bytes are preserved at {quarantine_path}."
            ),
            "ja": (
                f"需要対象指定の一覧ファイルが破損していたため隔離しました。{count_ja}。破損した原本は "
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
    "DEMAND_TARGET_DOC_KIND",
    "MAX_REASON_CHARS",
    "MAX_TOKEN_CHARS",
    "DemandTargetMapping",
    "DemandTargetRefused",
    "DemandTargetStoreError",
    "MappingEvent",
    "RegistryUnavailableError",
    "active_inpx_sha256",
    "candidate_key",
    "demand_targets_dir",
    "fresh_index_payload",
    "get_mapping",
    "index_payload",
    "list_mappings",
    "record_mapping",
    "registry_health_status",
    "registry_path",
    "revoke_mapping",
    "summarize",
]

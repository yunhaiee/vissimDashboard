"""Genesis as a product feature — the OSM → draft → emit → **gate** → publish job.

Roadmap v2 §5.B row **B1** (wire the genesis chain into the app) and §5.A row
**A4** (the gate is not optional). Until this module, the whole chain ran only
from ``backend/tools/genesis_osm_draft.py`` + ``tools/genesis_validation_gate.py``:
it worked, and a consultant had no button. Worse for A4, the gate ran only when
a human remembered to run it, which makes "the validation gate is what makes AI
construction sellable" (``tk-requirements-2026-07-31.md`` §R1.5) a habit rather
than a property.

**The gate is the exit door, structurally.**
:func:`gate_and_publish` is the ONLY function in this package that can turn a
generated artifact into a workspace dataset, and it does three things in a fixed
order that no caller can rearrange:

1. it RUNS the gate itself (``services.genesis.gate.run_gate``) — it takes no
   verdict parameter and no "skip" flag, so a caller cannot supply one;
2. it stores the full verdict + the rendered report next to the artifact,
   whatever the verdict is (a refusal is a deliverable, not an error);
3. only then, and only when the verdict is not ``REFUSED``, does it call
   :func:`_register_as_active_dataset` — which has exactly ONE call site in this
   module, inside that function, after that branch.

``tests/test_genesis_jobs.py`` pins all three as source-level facts as well as
behaviours, because "we always remember to call the gate first" is a convention
and this unit exists to stop it being one.

**What the three terminal states mean** (the honest tri-state R1.5 demands, not
a pass/fail binary):

``refused``
    A refuse-tier check PROVED a fault. The artifact stays in the job's work
    directory, downloadable and explained, and is never registered as a dataset.
``provisional``
    No refuse-tier fault, but at least one refuse-tier check could not run — on
    a fresh generation this is always V1, because a generation job may not open
    COM (see below) and V1 is the only check that proves a real Vissim opens the
    file at all. It publishes, and the reason is carried on the record so the UI
    can say WHY it is provisional rather than showing a green tick.
``published``
    Every refuse-tier check genuinely passed, V1 included. Reachable only after a
    real ``LoadNet``'s evidence has been fed back in — either attested through
    :func:`attach_live_check` or produced by a live-check job
    (:func:`run_live_check_job`).
``checked``
    Not a verdict at all: a LIVE-CHECK job's own terminal state (unit #33). It
    produced V1's evidence and handed it to its parent's re-gate; the verdict
    that evidence changed lives on the PARENT.

**No COM in a GENERATION, and that is why ``provisional`` exists.** A generation
job runs in a worker thread inside the API process; ``docs/phase2/
00-EXECUTION-GUIDE.md`` §1 and this project's CLAUDE.md both forbid COM outside
the dedicated lane, so nothing on the generation path imports
``services.vissim_com`` and a fresh generation can never answer V1. Its evidence
arrives afterwards, in the ``state.json`` shape
``tools/genesis_open_live_check.py`` writes and
``tools/genesis_validation_gate.py`` already reads — and ``gate.py``'s sha256
identity rule, not this module, decides whether it is evidence about THIS file.

**Two job types are the exception, and both are jobs for exactly that reason.**
:func:`run_genesis_fix_job` (B5-2 stage 2) opens a real ``VissimEditSession`` to
apply one already-approved verb, and :func:`run_live_check_job` (unit #33) opens
a real Vissim to answer V1's own question — does this artifact LOAD? Every
import that reaches COM is function-local (:func:`_build_fix_edit_session`,
:func:`_build_live_check_probe`, and the runners' own imports), so a plain
``import services.genesis_jobs`` still never touches COM; only actually RUNNING
one of those jobs does, on that job's own worker thread, never a request handler
— the same job-queue discipline CLAUDE.md requires everywhere else COM is
touched. Both COM legs are single named seams that tests replace, which is what
lets the whole of both runners be exercised offline.

**V1's evidence therefore arrives two ways, and they are labelled apart.**
:func:`attach_live_check` takes a ``state.json`` over HTTP — an operator's
attestation, ``basis: "operator_attested"``, see below.
:func:`create_live_check_job` + :func:`run_live_check_job` produce the SAME
record here, by opening the file in this process's own job queue, and label it
``basis: "app_observed"``. The second is genuinely stronger — nothing was
claimed, something was measured — and it is still not a proof that survives a
hostile workspace, so it gets its own word rather than either of the other two.
The judgement is identical in both cases: ``gate.py``'s ``LoadOracle`` and its
sha256 identity rule, unmodified, decide what the evidence is worth.

**What that rule does and does not prove — stated precisely, because an earlier
revision of this docstring claimed "nothing here can fake it" and an adversarial
pass proved otherwise.** The sha256 rule establishes that the evidence is ABOUT
this artifact. It cannot establish that Vissim ever ran: the state.json arrives
over HTTP, and a caller who knows the artifact (its own sha is in the job
record) can hand-write ``load_ok: true`` around it. There is no cryptographic
path from "a JSON a client uploaded" to "PTV Vissim opened this file", and
pretending otherwise would be the exact silent-wrong-answer failure this product
exists to prevent.

So attached evidence is modelled as what it actually is: an **operator
attestation**, the same shape as the Daejeon amber (a reviewer's declaration
carrying provenance, never a measurement the document made). It is recorded with
WHO attested it and when, it is labelled ``operator_attested`` everywhere it
surfaces — job record, gate summary, report, UI — and a verdict resting on it is
never dressed as machine-proven. That is a real and useful state: an accountable
human claim beats an unqualified grey box. It is simply not the same thing as
the app having watched Vissim open the file, and the two must never read alike.

**Downgrades are honoured, not hidden.** Attaching live evidence can make a
verdict WORSE (a probe that recorded ``load_ok: false`` turns V1 from
NOT_CHECKABLE into FAIL). If that happens to a job whose artifact was already
registered, the publication is REVOKED — but only when the workspace's active
dataset is still byte-identical to this artifact. Someone else's later upload is
never deleted to tidy up our own bookkeeping; the revocation is recorded on the
job either way.

**Side inputs — the optional layers, entering by the same door.** A generation
may carry demand (+ its fleet) and signals, supplied as DATA the geometry cannot
imply: the site→leg→link join and the approach→phase mapping are the two facts
``services/genesis``'s own modules refuse to guess. They arrive the way the
``.osm`` does and no other way — staged through :func:`stage_side_input` (auth,
size cap, structural validation BEFORE a token exists), frozen into the job's
work directory at enqueue, and re-read from that frozen copy by the runner. The
composition itself is ``services.genesis.compose.compose_layers``, the extracted
form of the four-layer build that a real Vissim 2026 SP09 opened (``445477d``);
this module chooses none of it.

Two properties matter here and are tested:

* **absent side inputs change nothing.** ``compose_layers`` is not called, the
  emitter is handed the same three ``None``s it was handed before this feature
  existed, and a geometry-only generation is byte-identical to what it was;
* **a side input that refuses refuses the GENERATION.** There is no
  emit-what-we-can path: a caller who supplied counts and gets back a network
  with no demand has had their data silently dropped, which is the one failure
  this project ranks above all others. The job lands ``error`` with
  ``side_inputs_refused`` and every reason named, and ``layers.json`` beside it
  carries the full findings even though no artifact exists.

**Workspace discipline.** Every artifact lives under
``<workspace>/genesis/``: staged uploads, job work directories, and the job
registry. Two projects cannot see each other's generations physically, not by
filter. The job runner thread re-binds the submitting workspace explicitly
(``with workspaces.bind_workspace(...)``) because a ``threading.Thread`` does
not inherit the request's ContextVars and ``current_workspace_id()`` raises
rather than guessing.

**Registry durability** follows ``services/simulation_runs.py``'s U6 shape in
miniature: one module ``RLock`` across every read-modify-write, one writer, an
atomic same-directory temp-then-``os.replace``. It differs deliberately in one
place: a registry that does not parse is REFUSED (``GenesisRegistryError``)
rather than quarantined-and-restarted. Refusing to write is the safe half of
U6's behaviour and needs no recovery machinery; the operator moves the file
aside by hand, and until they do, nothing overwrites it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence
from uuid import uuid4

from services import datasets, observations, workspaces
from services.dataimport.adapters.daejeon import DaejeonCountAdapter
from services.dataimport.base import AdapterError, ImportResult, IssueSeverity
from services.genesis import side_inputs as side_inputs_module
from services.genesis.compose import NO_LAYERS, ComposedLayers
from services.genesis.connectivity import DANGLING_MODEL_BOUNDARY
from services.genesis.draft import (
    NODE_HALF_SIZE_M,
    DraftNetwork,
    build_draft,
    classify_turn,
    connector_lane_pairs,
)
from services.genesis.emit import EmitRefused, emit_inpx
from services.genesis.gate import (
    OVERLAP_DECIMALS,
    SHORT_LINK_FLOOR_M,
    ZERO_LENGTH_M,
    CheckState,
    GateResult,
    GateSettings,
    GateVerdict,
    LoadOracle,
    render_report,
    run_gate,
)
from services.genesis.osm import (
    CONVERTER_FIXES,
    SKIP_CONFIRMED_REAL_WORLD_DEAD_END,
    parse_osm_file,
    requested_bbox_from_provenance,
    requested_polygon_from_provenance,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, see _build_fix_edit_session
    from services.agent.edit_session import VissimEditSession
    from services.genesis.osm import OsmExtract

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Names, limits, states
# ---------------------------------------------------------------------------

GENESIS_DIRNAME = "genesis"
JOBS_DIRNAME = "jobs"
STAGING_DIRNAME = "staging"
#: Staged side inputs (demand / signals). A SEPARATE tree from ``staging``: the
#: two carry different kinds of file with different caps, and a token minted for
#: one must never resolve in the other.
SIDE_STAGING_DIRNAME = "side-inputs"
REGISTRY_FILENAME = "registry.json"

#: What the gate wrote about this artifact, beside the artifact.
GATE_JSON_FILENAME = "gate.json"
GATE_REPORT_FILENAME = "gate.report.md"

#: Where a verdict that is about to be REPLACED is preserved (unit #33).
#: ``gate.json`` is rewritten by every re-gate — attaching an operator's
#: attestation always could, and a live-check job now does it from inside the
#: app — and a verdict overwritten without a trace is exactly the kind of
#: evidence loss this gate exists to prevent. Chosen shape: a sibling DIRECTORY
#: holding ``gate.0001.json``, ``gate.0002.json``, … rather than ``gate.json.1``
#: files beside the artifact, because the job directory is also the download
#: surface (:func:`artifact_path` resolves fixed names in it) and a growing
#: family of ``gate.json.N`` siblings would sit in that same namespace. The
#: archived copies are BYTE copies of what was on disk, never a re-serialisation
#: of a parsed verdict.
GATE_HISTORY_DIRNAME = "gate_history"

#: A live-check job's own evidence file, in ITS work directory: exactly the
#: shape ``tools/genesis_open_live_check.py`` writes to
#: ``runtime/genesis-open-live-check/state.json`` and exactly the shape
#: ``gate.LoadOracle.from_probe_state`` reads. Written whatever the probe
#: reported — a refused load is evidence too.
LIVE_CHECK_STATE_FILENAME = "live-check.state.json"

#: What the optional layers reported, beside the artifact. A FIXED name — like
#: the two above and unlike the ``<name>.*`` artifacts — precisely so it exists
#: for a generation that produced NO artifact: a side input that refused still
#: has to be able to explain itself, and it explains itself here.
LAYERS_JSON_FILENAME = "layers.json"

#: How a staged side input's kind and original name are remembered.
SIDE_INPUT_META_FILENAME = "meta.json"

#: A third kind staged in the SAME tree as ``demand``/``signals``, one door
#: over from both — a counts workbook is not itself a ``genesis.demand.v1``
#: document (it carries no ``approach_links``; only a human can supply the
#: join), so it cannot be validated by ``side_inputs.parse_side_input`` and is
#: not one of ``side_inputs.KINDS``. What it shares with the other two is the
#: staging discipline: authenticated, size-capped, parsed BEFORE a token is
#: handed out, swept by the same TTL. ``POST /api/genesis/demand-inputs/build``
#: is the only consumer of a ``counts`` token, and it never forwards the token
#: itself into a job — it reads the staged ``ImportResult`` back, joins it with
#: a caller-supplied ``approach_links``, and stages the RESULT as a proper
#: ``demand`` side input through :func:`stage_side_input`, the one door a job
#: actually accepts.
COUNTS_KIND = "counts"
#: The parsed ``ImportResult`` (``model_dump(mode="json")``), stored beside the
#: staged workbook's ``meta.json``. Storing the NEUTRAL result rather than
#: re-parsing the workbook on every build call means "the counts I parsed" and
#: "the counts I built a demand document from" are provably the same bytes.
COUNTS_DOC_FILENAME = "counts.json"

#: The frozen copy inside the job's work directory, per kind.
_SIDE_INPUT_STORED_AS = {
    side_inputs_module.KIND_DEMAND: "demand.input.json",
    side_inputs_module.KIND_SIGNALS: "signals.input.json",
}

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
#: A refuse-tier check proved a fault. Artifact retained, never registered.
STATUS_REFUSED = "refused"
#: Published, but a refuse-tier check could not run (V1 without live evidence).
STATUS_PROVISIONAL = "provisional"
#: Published with every refuse-tier check genuinely passed.
STATUS_PUBLISHED = "published"
#: No artifact exists — the .osm could not become a network at all.
STATUS_ERROR = "error"
#: A LIVE-CHECK job finished its probe (unit #33). Deliberately not one of the
#: three gate verdicts: a live-check job never gates an artifact of its own, it
#: produces V1's evidence and hands it to the PARENT's re-gate — the verdict
#: lives on the parent, and a status here that read ``published``/``refused``
#: would claim a judgement this job never made. ``load_ok: false`` still lands
#: ``checked``: the probe did its work and the gate answered.
STATUS_CHECKED = "checked"

TERMINAL_STATUSES = frozenset(
    {STATUS_REFUSED, STATUS_PROVISIONAL, STATUS_PUBLISHED, STATUS_ERROR, STATUS_CHECKED}
)
#: The states in which an artifact exists on disk and can be re-gated.
GATED_STATUSES = frozenset({STATUS_REFUSED, STATUS_PROVISIONAL, STATUS_PUBLISHED})

PHASE_QUEUED = "queued"
PHASE_READING_OSM = "reading_osm"
PHASE_DRAFTING = "drafting"
PHASE_EMITTING = "emitting"
PHASE_GATING = "gating"
PHASE_PUBLISHING = "publishing"
PHASE_DONE = "done"
#: A fix job's own phase, between ``PHASE_QUEUED`` and ``PHASE_GATING`` --
#: the COM turn that applies the one approved verb (:func:`run_genesis_fix_job`).
#: Ordinary generation jobs never carry this phase; they have no equivalent
#: step at all (they never touch COM -- see the module docstring).
PHASE_APPLYING_FIX = "applying_fix"
#: A LIVE-CHECK job's own COM phase (unit #33): the one ``LoadNet`` turn that
#: produces V1's evidence, between ``PHASE_QUEUED`` and ``PHASE_GATING`` (the
#: PARENT's re-gate).
PHASE_LIVE_CHECKING = "live_checking"

#: What a V1 that went green on ATTACHED evidence actually rests on. Carried on
#: the job record, in the gate summary and into the UI, so a verdict resting on
#: a human's claim never reads like one the app proved for itself. The app
#: cannot prove Vissim ran from a JSON a client uploaded (module docstring), and
#: naming the basis is how that stays visible instead of becoming folklore.
ATTESTATION_BASIS = "operator_attested"

#: What a V1 that went green on a LIVE-CHECK JOB's evidence rests on (unit #33),
#: and the reason it is a different word from :data:`ATTESTATION_BASIS`. Here the
#: app itself opened the file in a real Vissim, on its own job queue, and wrote
#: the record from what ``LoadNet`` returned — nothing arrived over HTTP and no
#: human's claim is involved. That is a genuinely stronger basis than an
#: attestation, and collapsing the two into one word would either flatter an
#: operator's claim or slander the app's own measurement. It is NOT a claim of
#: cryptographic proof: the record is still just a file this process wrote, and
#: anyone with the workspace directory can edit it after the fact.
LIVE_CHECK_BASIS = "app_observed"

#: The lineage ``kind`` on a LIVE-CHECK job's ``derived_from``. A fix job's
#: lineage carries no ``kind`` key at all (it predates this one), so presence
#: alone discriminates the two — the same "presence is itself the fact"
#: discipline ``requested_region`` and ``derived_from`` already use here.
LIVE_CHECK_LINEAGE_KIND = "live_check"

#: Progress the UI shows per phase. Coarse and honest: these are the real step
#: boundaries this runner has, and there is no way to sample inside
#: ``build_draft`` without perturbing it, so nothing finer is invented.
_PHASE_PROGRESS = {
    PHASE_QUEUED: 0.0,
    PHASE_READING_OSM: 0.1,
    PHASE_DRAFTING: 0.35,
    PHASE_EMITTING: 0.7,
    PHASE_APPLYING_FIX: 0.5,
    PHASE_LIVE_CHECKING: 0.5,
    PHASE_GATING: 0.85,
    PHASE_PUBLISHING: 0.95,
    PHASE_DONE: 1.0,
}

#: Artifact base name: a plain filename stem that cannot carry a path separator,
#: a ``..`` segment or a drive letter. Same defensive shape as
#: ``services/workspaces.py``'s ``_WORKSPACE_ID_RE`` — validated here AND
#: contained by :func:`job_dir`'s resolve()+relative_to check.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Job/upload ids. Hex only, for the same reason.
_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

#: How many findings per check the API payload carries inline. The gate's own
#: report caps rendering at 12; a REFUSED network can carry hundreds (the
#: 2026-08-11 Murdoch run had 150 isolated-zone findings), and shipping every
#: one into a browser payload helps nobody. What is NOT allowed is a silent
#: drop: the payload states how many were omitted, and ``gate.json`` on disk
#: always holds all of them and is downloadable.
MAX_FINDINGS_IN_PAYLOAD = 25

#: Staged uploads older than this are swept on the next upload.
STAGING_TTL_SECONDS = 24 * 3600

_REGISTRY_LOCK = threading.RLock()

#: Per-(workspace, job) locks around :func:`gate_and_publish`'s critical section
#: (unit #33 finding F1). **Invariant this protects:** two callers re-gating the
#: SAME job at once (the live-check job path and the pre-existing
#: :func:`attach_live_check` route both land here for their shared parent, and
#: nothing stops a double-click in two tabs from starting both) can never
#: interleave "read the outgoing gate.json → archive it → write the new verdict
#: → update the registry summary" — so a verdict can never be lost between two
#: racing writers, and ``gate.json`` on disk can never disagree with the
#: registry's summary for longer than it takes one caller to finish. Keyed per
#: job (not one global lock) so unrelated jobs still gate in parallel — nothing
#: COM runs inside the critical section, so the only cost of holding it is gate
#: computation plus a handful of file writes. Entries are never evicted (a job's
#: lock is cheap and its lifetime is bounded by the job count, not by traffic),
#: guarded by :data:`_GATE_LOCKS_GUARD` so two threads minting a NEW job's first
#: lock at once cannot each install their own and let a third caller pick either
#: one.
_GATE_LOCKS_GUARD = threading.Lock()
_GATE_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def _gate_lock(job_id: str) -> threading.Lock:
    """The one lock for THIS workspace's THIS job, minted on first use.

    Keyed on ``(workspaces.current_workspace_id(), job_id)`` rather than
    ``job_id`` alone, matching every other workspace-scoped lookup in this
    module (:func:`registry_path` and friends) — ``job_id`` is already a
    globally unique hex token, so the workspace half changes no collision
    behaviour today, but it keeps this lock keyed the same way its own
    registry file is found, rather than by an assumption that will not need
    re-checking if that ever stops being true.

    A plain :class:`threading.Lock`, not an :class:`threading.RLock`: nothing
    in this module calls :func:`gate_and_publish` re-entrantly for the SAME
    job on the SAME thread (every caller — a generation's own runner, a fix
    job's own runner, ``attach_live_check``, a live-check job's runner — calls
    it exactly once per invocation, and a live-check job always re-gates its
    PARENT while a fix job always gates its SIBLING, a different key). An RLock
    would silently paper over a future caller that became reentrant instead of
    deadlocking on it, which is the wrong failure mode for a lock whose entire
    job is to make concurrent access visible.
    """
    key = (workspaces.current_workspace_id(), job_id)
    with _GATE_LOCKS_GUARD:
        lock = _GATE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _GATE_LOCKS[key] = lock
        return lock


#: Registry I/O retries for the Windows transient-lock family. A polling browser
#: (or an antivirus scanner, or the search indexer) holds a read handle on the
#: registry for microseconds, and that is enough to make a concurrent
#: ``os.replace`` fail with ``WinError 5``. Same reasoning and the same shape as
#: ``services/datasets.py``'s ``_RENAME_RETRIES`` and
#: ``services/simulation_runs.py``'s ``_READ_RETRY_ATTEMPTS``; total added
#: latency in the worst case is ~100 ms, and only on a failing operation.
_IO_RETRY_ATTEMPTS = 5
_IO_RETRY_DELAY_S = 0.02


class GenesisJobError(RuntimeError):
    """Base class for every failure this module raises."""


class GenesisJobNotFoundError(GenesisJobError):
    """No such job in THIS workspace. A job belonging to another project is
    reported as simply not existing — same shape as an unknown id, so the
    answer cannot be used to probe which job ids exist on the box."""


class GenesisRegistryError(GenesisJobError):
    """The registry file exists but could not be read, so writing would destroy
    it. Never converted into an empty registry."""


class GenesisPublicationRecordError(GenesisJobError):
    """The artifact WAS registered as the active dataset, but the registry write
    that records ``status=published/provisional`` + ``publication`` could not be
    completed.

    Carried separately from a generic failure precisely so the runner does NOT
    mislabel the job ``error`` / ``generation_failed`` (whose contract is "no
    artifact exists") when the dataset is, in fact, live in the workspace. The
    invariant this protects: the job's recorded status must never contradict
    what is actually live.
    """

    def __init__(
        self,
        job_id: str,
        *,
        status: str,
        gate: dict[str, Any] | None,
        publication: dict[str, Any] | None,
        cause: BaseException,
    ) -> None:
        super().__init__(
            f"Job {job_id} registered its dataset but the publication record could not be "
            f"written ({type(cause).__name__}: {cause})."
        )
        self.job_id = job_id
        self.status = status
        self.gate = gate
        self.publication = publication


class StagedOsmNotFoundError(GenesisJobError):
    """An upload token that is unknown, malformed, or expired."""


class GenesisRefused(GenesisJobError):
    """The ``.osm`` could not become a network at all.

    Distinct from a gate refusal in both direction and meaning: this one says
    "there is no artifact", the gate's says "there IS an artifact and it has a
    proven fault". Collapsing them would make a client unable to tell "your
    extract is unusable" from "the network we built has 8 unreachable pockets".
    """

    def __init__(self, code: str, reasons: list[str]) -> None:
        super().__init__(f"{code}: {'; '.join(reasons) or code}")
        self.code = code
        self.reasons = reasons


class _FixApplyFailed(GenesisJobError):
    """One step of :func:`run_genesis_fix_job`'s single-verb apply failed.

    Private: this exists only so :func:`_run_genesis_fix_job_locked` can build
    the job record's ``fix_apply_failed:{step}`` refusal code in one place
    (:attr:`step`) with its human-readable reasons (:attr:`reasons`), rather
    than duplicating that format string at every raise site (preflight
    blocked, dispatch itself failed, postwrite blocked). Never crosses this
    module's own boundary — a caller sees the resulting job record, not this
    exception.
    """

    def __init__(self, step: str, reasons: list[str]) -> None:
        super().__init__(f"fix_apply_failed:{step}: {'; '.join(reasons) or step}")
        self.step = step
        self.reasons = reasons


class _LiveCheckFailed(GenesisJobError):
    """The live-check job could not PRODUCE evidence (unit #33).

    Strictly "the probe never answered": COM was unavailable, the record came
    back without a ``load_ok`` at all, the job's own derivation was missing.
    A probe that answered ``load_ok: false`` is NOT this — that is evidence, it
    goes to the gate like any other, and the job finishes ``checked`` with the
    parent carrying whatever verdict the judge reached. Conflating the two would
    turn "Vissim refused this network" into "our tooling broke", which is the
    single most expensive lie this feature could tell.

    Private, for the same reason :class:`_FixApplyFailed` is: it exists so the
    runner builds its ``live_check_failed:{step}`` refusal code in one place.
    """

    def __init__(self, step: str, reasons: list[str]) -> None:
        super().__init__(f"live_check_failed:{step}: {'; '.join(reasons) or step}")
        self.step = step
        self.reasons = reasons


# ---------------------------------------------------------------------------
# Paths — all under the workspace's own tree
# ---------------------------------------------------------------------------


def genesis_dir() -> Path:
    """``<current workspace>/genesis``. Derived per call from the bound
    workspace, like every workspace-scoped path since unit 1.4."""
    return workspaces.workspace_dir(workspaces.current_workspace_id()) / GENESIS_DIRNAME


def registry_path() -> Path:
    return genesis_dir() / REGISTRY_FILENAME


def staging_dir() -> Path:
    return genesis_dir() / STAGING_DIRNAME


def side_staging_dir() -> Path:
    return genesis_dir() / SIDE_STAGING_DIRNAME


def jobs_root() -> Path:
    return genesis_dir() / JOBS_DIRNAME


def job_dir(job_id: str) -> Path:
    """The work directory for ``job_id``, validated twice (regex, containment).

    Never trusts the id shape alone: :data:`_ID_RE` already rules out a path
    separator, and the resolve()+relative_to below is the independent second
    check, exactly as ``services/workspaces.py``'s ``workspace_dir`` does.
    """
    if not job_id or not _ID_RE.match(job_id):
        raise GenesisJobNotFoundError(f"Unknown genesis job: {job_id!r}")
    root = jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    target = (resolved_root / job_id).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:  # pragma: no cover - the regex rules this out
        raise GenesisJobNotFoundError(f"Unknown genesis job: {job_id!r}") from None
    return target


# ---------------------------------------------------------------------------
# Staging an uploaded .osm
# ---------------------------------------------------------------------------


def stage_osm(filename: str, content: bytes) -> dict[str, Any]:
    """Stage one uploaded ``.osm`` and return ``{token, filename, bytes}``.

    The bytes are proved to be an OSM XML document BEFORE the token is handed
    out: a caller that uploads a .zip or a truncated download finds out while
    they are still looking at the upload, not two steps later inside a job whose
    only output is a refusal.

    Deliberately NOT a full ``parse_osm_file``: that walks the whole document
    twice and is the job's own first phase. This is the cheap "is this the right
    kind of file at all" check — well-formed XML whose root element is ``osm``.
    """
    name = Path(str(filename or "")).name.strip()
    if not name:
        raise GenesisRefused("genesis_upload_no_filename", ["The upload carries no file name."])
    if Path(name).suffix.lower() not in (".osm", ".xml"):
        raise GenesisRefused(
            "genesis_upload_not_osm",
            [f"{name!r} is not a .osm/.xml file. Genesis reads a local OpenStreetMap extract."],
        )
    if not content.strip():
        raise GenesisRefused("genesis_upload_empty", [f"{name!r} is empty."])

    root_tag = _xml_root_tag(content)
    if root_tag != "osm":
        raise GenesisRefused(
            "genesis_upload_not_osm",
            [
                f"{name!r} does not parse as an OSM XML document"
                + (f" (root element is <{root_tag}>)." if root_tag else " (not well-formed XML).")
            ],
        )

    _purge_stale_staging()
    token = uuid4().hex
    target_dir = staging_dir() / token
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(content)
    return {"token": token, "filename": name, "bytes": len(content)}


def _xml_root_tag(content: bytes) -> str | None:
    """The document element's tag, or ``None`` when the bytes are not XML.

    ``iterparse`` with a ``start`` event so a 400 MB extract is not fully parsed
    just to read its first tag.
    """
    import xml.etree.ElementTree as ET
    from io import BytesIO

    try:
        for _event, element in ET.iterparse(BytesIO(content), events=("start",)):
            tag = str(element.tag)
            return tag.rsplit("}", 1)[-1]
    except ET.ParseError:
        return None
    return None


def staged_osm_path(token: str) -> Path:
    """The staged ``.osm`` for ``token``, validated twice."""
    if not token or not _ID_RE.match(token):
        raise StagedOsmNotFoundError("Unknown or expired upload token. Please upload the .osm again.")
    root = staging_dir()
    if not root.is_dir():
        raise StagedOsmNotFoundError("Unknown or expired upload token. Please upload the .osm again.")
    resolved_root = root.resolve()
    target = (resolved_root / token).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:  # pragma: no cover - the regex rules this out
        raise StagedOsmNotFoundError("Unknown or expired upload token.") from None
    if not target.is_dir():
        raise StagedOsmNotFoundError("Unknown or expired upload token. Please upload the .osm again.")
    files = sorted(item for item in target.iterdir() if item.is_file())
    if not files:
        raise StagedOsmNotFoundError("Unknown or expired upload token. Please upload the .osm again.")
    return files[0]


def discard_staged_osm(token: str) -> None:
    if not token or not _ID_RE.match(token):
        return
    root = staging_dir()
    target = root / token
    if target.is_dir() and target.parent == root:
        shutil.rmtree(target, ignore_errors=True)


def _purge_stale_staging(max_age_seconds: int = STAGING_TTL_SECONDS) -> None:
    for root in (staging_dir(), side_staging_dir()):
        if not root.is_dir():
            continue
        now = time.time()
        for child in root.iterdir():
            try:
                if child.is_dir() and now - child.stat().st_mtime > max_age_seconds:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:  # pragma: no cover - best effort sweep
                continue


# ---------------------------------------------------------------------------
# Staging a side input (demand / signals) — the SAME door, one tree over
# ---------------------------------------------------------------------------


def stage_side_input(kind: str, filename: str, content: bytes) -> dict[str, Any]:
    """Stage one side-input document and return ``{token, kind, …, summary}``.

    Structurally validated BEFORE the token exists, for the reason
    :func:`stage_osm` proves the ``.osm`` is OSM XML: a caller who uploads the
    wrong document, or one with a typo in a key, finds out while they are still
    looking at the upload rather than inside a job whose only output is a
    refusal. ``services.genesis.side_inputs`` owns what "valid" means; this
    function owns where the bytes land.

    The ``summary`` in the response is COUNTED from the document, never sampled:
    a caller has to be able to see that the counts they meant to send are the
    counts that arrived (30,208 records, four sites, three classes) before they
    spend a generation finding out.
    """
    name = Path(str(filename or "")).name.strip()
    if not name:
        raise GenesisRefused(
            "genesis_side_input_no_filename", ["The upload carries no file name."]
        )
    if len(content) > side_inputs_module.MAX_SIDE_INPUT_BYTES:
        raise GenesisRefused(
            "genesis_side_input_too_large",
            [
                f"{name!r} is {len(content)} bytes; the side-input limit is "
                f"{side_inputs_module.MAX_SIDE_INPUT_BYTES} bytes "
                f"({side_inputs_module.MAX_SIDE_INPUT_BYTES // (1024 * 1024)} MB)."
            ],
        )
    try:
        document = side_inputs_module.parse_side_input(kind, content)
    except side_inputs_module.SideInputRefused as exc:
        raise GenesisRefused(exc.code, exc.reasons) from exc

    _purge_stale_staging()
    token = uuid4().hex
    target_dir = side_staging_dir() / token
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    summary = document.summary()
    meta = {
        "kind": kind,
        "filename": name,
        "bytes": len(content),
        "sha256": digest,
        "staged_at": _now_iso(),
        # Persisted (not just returned) so GET /api/genesis/side-inputs can
        # list every staged document's summary without re-parsing it -- a
        # listing must stay cheap even when a staged demand document carries
        # 30,000 counts.
        "summary": summary,
    }
    (target_dir / SIDE_INPUT_META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    return {"token": token, **meta}


def staged_side_input(token: str) -> dict[str, Any]:
    """A staged side input's recorded kind, name and path, validated twice.

    Returns the PATH for the caller that copies it into a job (this module and
    ``routers/genesis.py``), and never puts it on a job record — the record
    crosses the HTTP boundary and no response of ours publishes a server path.
    """
    if not token or not _ID_RE.match(token):
        raise StagedOsmNotFoundError(
            "Unknown or expired side-input token. Please upload the document again."
        )
    root = side_staging_dir()
    if not root.is_dir():
        raise StagedOsmNotFoundError(
            "Unknown or expired side-input token. Please upload the document again."
        )
    resolved_root = root.resolve()
    target = (resolved_root / token).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:  # pragma: no cover - the regex rules this out
        raise StagedOsmNotFoundError("Unknown or expired side-input token.") from None
    meta_path = target / SIDE_INPUT_META_FILENAME
    if not target.is_dir() or not meta_path.is_file():
        raise StagedOsmNotFoundError(
            "Unknown or expired side-input token. Please upload the document again."
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StagedOsmNotFoundError(
            "The staged side input's record could not be read. Please upload it again."
        ) from exc
    document = target / str(meta.get("filename") or "")
    if not document.is_file():
        raise StagedOsmNotFoundError(
            "Unknown or expired side-input token. Please upload the document again."
        )
    return {
        "token": token,
        "kind": str(meta.get("kind") or ""),
        "filename": document.name,
        "bytes": int(meta.get("bytes") or document.stat().st_size),
        "sha256": str(meta.get("sha256") or ""),
        "path": document,
    }


def discard_staged_side_input(token: str) -> None:
    if not token or not _ID_RE.match(token):
        return
    root = side_staging_dir()
    target = root / token
    if target.is_dir() and target.parent == root:
        shutil.rmtree(target, ignore_errors=True)


def list_staged_side_inputs() -> list[dict[str, Any]]:
    """Every currently staged side input (``demand``/``signals``/``counts``) in
    THIS workspace, newest first.

    Reads each entry's ``meta.json`` rather than re-parsing the staged document
    — the summary was computed and persisted at stage time (see
    :func:`stage_side_input` and :func:`stage_counts`), so a listing stays cheap
    even when a staged demand document carries tens of thousands of counts.

    Best-effort per entry: a token directory whose ``meta.json`` cannot be read
    (a half-written stage, a concurrent sweep) is skipped rather than failing
    the whole listing — the same "a listing must not 500 over one bad record"
    rule :func:`list_jobs` follows. Never raises for a missing staging tree.
    """
    root = side_staging_dir()
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir() or not _ID_RE.match(child.name):
            continue
        meta_path = child / SIDE_INPUT_META_FILENAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        entries.append(
            {
                "token": child.name,
                "kind": str(meta.get("kind") or ""),
                "filename": str(meta.get("filename") or ""),
                "bytes": int(meta.get("bytes") or 0),
                "sha256": str(meta.get("sha256") or ""),
                "staged_at": str(meta.get("staged_at") or ""),
                "summary": meta.get("summary") if isinstance(meta.get("summary"), dict) else {},
            }
        )
    entries.sort(key=lambda item: item["staged_at"], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Staging a counts workbook — the demand side input's raw half
# ---------------------------------------------------------------------------
#
# A counts workbook is not a `genesis.demand.v1` document (it carries no
# `approach_links`; the join is a fact only a human can supply), so it does not
# go through `side_inputs.parse_side_input` and is not one of `side_inputs.KINDS`.
# It shares the staging tree and the staging discipline with `demand`/`signals`
# -- authenticated, size-capped, parsed BEFORE a token exists, swept by the same
# TTL -- and `discard_staged_side_input` already discards it (the function only
# looks at the token, never the kind).


def stage_counts(filename: str, content: bytes) -> dict[str, Any]:
    """Stage one Daejeon counts workbook and return ``{token, kind, …, summary}``.

    Parsed with :class:`~services.dataimport.adapters.daejeon.DaejeonCountAdapter`
    BEFORE the token exists, for the same reason :func:`stage_osm` proves the
    ``.osm`` is OSM XML: a caller who uploads the wrong workbook finds out while
    they are still looking at the upload. The adapter's own contract is
    "report, do not raise" (``services/dataimport/base.py``), so a workbook with
    REFUSE-tier issues still stages -- the summary shows the issues verbatim,
    unsoftened, so the caller can see exactly what needs fixing before trying a
    different file. Only a structurally unreadable upload (wrong extension,
    empty body, an .xlsx openpyxl cannot open at all) is refused outright,
    without a token -- the same split :func:`stage_side_input` draws between "the
    bytes are unusable" and "the document is usable but reports problems".
    """
    name = Path(str(filename or "")).name.strip()
    if not name:
        raise GenesisRefused("genesis_counts_no_filename", ["The upload carries no file name."])
    if Path(name).suffix.lower() not in (".xlsx", ".xlsm"):
        raise GenesisRefused(
            "genesis_counts_not_workbook",
            [
                f"{name!r} is not a .xlsx/.xlsm workbook. The Daejeon count adapter reads an "
                "Excel export, one workbook per intersection."
            ],
        )
    if not content.strip():
        raise GenesisRefused("genesis_counts_empty", [f"{name!r} is empty."])
    if len(content) > side_inputs_module.MAX_SIDE_INPUT_BYTES:
        raise GenesisRefused(
            "genesis_counts_too_large",
            [
                f"{name!r} is {len(content)} bytes; the limit is "
                f"{side_inputs_module.MAX_SIDE_INPUT_BYTES} bytes "
                f"({side_inputs_module.MAX_SIDE_INPUT_BYTES // (1024 * 1024)} MB)."
            ],
        )

    _purge_stale_staging()
    token = uuid4().hex
    target_dir = side_staging_dir() / token
    target_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = target_dir / name
    workbook_path.write_bytes(content)

    try:
        result = DaejeonCountAdapter().parse([workbook_path])
    except AdapterError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise GenesisRefused("genesis_counts_unreadable", [f"{name}: {exc}"]) from exc
    finally:
        # The workbook itself is not needed past this point -- only the neutral
        # ImportResult is staged, so a later build call reads back the SAME
        # parse rather than a workbook that could read differently a second
        # time (a different openpyxl version, a file touched on disk).
        try:
            if workbook_path.is_file():
                workbook_path.unlink()
        except OSError:  # pragma: no cover - best effort
            pass

    document = result.model_dump(mode="json")
    (target_dir / COUNTS_DOC_FILENAME).write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )

    digest = hashlib.sha256(content).hexdigest()
    summary = _counts_summary(result)
    meta = {
        "kind": COUNTS_KIND,
        "filename": name,
        "bytes": len(content),
        "sha256": digest,
        "staged_at": _now_iso(),
        "summary": summary,
    }
    (target_dir / SIDE_INPUT_META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    return {"token": token, **meta}


def staged_counts(token: str) -> dict[str, Any]:
    """A staged counts token's neutral :class:`ImportResult`, validated twice.

    Returns the parsed RESULT, never a path -- the caller (the demand-input
    build route) joins it with an ``approach_links`` mapping and stages the
    outcome as a proper ``demand`` side input; it has no reason to touch a
    server path at all.
    """
    if not token or not _ID_RE.match(token):
        raise StagedOsmNotFoundError(
            "Unknown or expired counts token. Please upload the workbook again."
        )
    root = side_staging_dir()
    if not root.is_dir():
        raise StagedOsmNotFoundError(
            "Unknown or expired counts token. Please upload the workbook again."
        )
    resolved_root = root.resolve()
    target = (resolved_root / token).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:  # pragma: no cover - the regex rules this out
        raise StagedOsmNotFoundError("Unknown or expired counts token.") from None
    meta_path = target / SIDE_INPUT_META_FILENAME
    doc_path = target / COUNTS_DOC_FILENAME
    if not target.is_dir() or not meta_path.is_file() or not doc_path.is_file():
        raise StagedOsmNotFoundError(
            "Unknown or expired counts token. Please upload the workbook again."
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StagedOsmNotFoundError(
            "The staged counts record could not be read. Please upload it again."
        ) from exc
    kind = str(meta.get("kind") or "")
    if kind != COUNTS_KIND:
        raise StagedOsmNotFoundError(
            f"That token names a {kind or 'unknown'!r} side input, not a counts one."
        )
    try:
        document = json.loads(doc_path.read_text(encoding="utf-8"))
        result = ImportResult.model_validate(document)
    except (OSError, ValueError) as exc:
        raise StagedOsmNotFoundError(
            "The staged counts could not be read. Please upload the workbook again."
        ) from exc
    return {
        "token": token,
        "kind": kind,
        "filename": str(meta.get("filename") or ""),
        "bytes": int(meta.get("bytes") or 0),
        "sha256": str(meta.get("sha256") or ""),
        "result": result,
    }


def _counts_summary(result: ImportResult) -> dict[str, Any]:
    """What a counts parse produced, COUNTED never sampled -- the same
    discipline :meth:`side_inputs.DemandSideInput.summary` follows. Sites carry
    their legs verbatim (never renamed, never reordered by anything but sort)
    so the mapping UI can build one row per leg with the id `generate_demand`
    will actually compare against.
    """
    sites: list[dict[str, Any]] = []
    for site in result.sites:
        legs = sorted({c.approach.leg_id for c in result.counts if c.site_id == site.site_id})
        sites.append({"site_id": site.site_id, "site_name": site.site_name, "legs": legs})
    starts = [c.interval_start for c in result.counts]
    ends = [c.interval_end for c in result.counts]
    window = (
        {"start": min(starts).isoformat(), "end": max(ends).isoformat()}
        if starts and ends
        else None
    )
    return {
        "adapter": result.adapter.name,
        "region": result.adapter.region,
        "source_files": list(result.source_files),
        "counts": len(result.counts),
        "sites": sites,
        "vehicle_classes": sorted({c.vehicle_class.code for c in result.counts}),
        "window": window,
        "issues": [
            {
                "severity": issue.severity.value,
                "code": issue.code,
                "message": issue.message,
                "subject": issue.subject,
                "context": issue.context,
            }
            for issue in result.issues
        ],
        "refusals": result.issue_codes(IssueSeverity.REFUSE),
        "ok": result.ok,
    }


# ---------------------------------------------------------------------------
# Registry — one lock, one writer, atomic replace
# ---------------------------------------------------------------------------


def _read_registry() -> dict[str, dict[str, Any]]:
    """Every job record in this workspace, keyed by id.

    Raises:
        GenesisRegistryError: the file exists but did not parse or could not be
            read. Callers that WRITE must let this propagate — overwriting a
            registry we could not read is how a transient I/O problem becomes
            permanent data loss (``services/simulation_runs.py``'s U6 lesson).
    """
    path = registry_path()
    if not path.exists():
        return {}

    raw: str | None = None
    last_error: OSError | None = None
    for attempt in range(_IO_RETRY_ATTEMPTS):
        try:
            raw = path.read_text(encoding="utf-8")
            break
        except OSError as exc:
            last_error = exc
            if attempt + 1 < _IO_RETRY_ATTEMPTS:
                time.sleep(_IO_RETRY_DELAY_S)
    if raw is None:
        raise GenesisRegistryError(
            f"The genesis job registry at {path} could not be read after {_IO_RETRY_ATTEMPTS} "
            f"attempts ({type(last_error).__name__}: {last_error}). Nothing will be written over "
            "it; move it aside by hand to start a fresh one."
        )

    try:
        payload = json.loads(raw)
    except ValueError as exc:
        # Not retried: a parse failure is a property of the bytes, and retrying
        # would only delay the same answer.
        raise GenesisRegistryError(
            f"The genesis job registry at {path} could not be read ({type(exc).__name__}: {exc}). "
            "Nothing will be written over it; move it aside by hand to start a fresh one."
        ) from exc
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, dict):
        raise GenesisRegistryError(
            f"The genesis job registry at {path} is not a {{'jobs': {{...}}}} object. "
            "Nothing will be written over it; move it aside by hand to start a fresh one."
        )
    return {
        str(key): record
        for key, record in jobs.items()
        if isinstance(record, dict)
    }


def _write_registry(jobs: dict[str, dict[str, Any]]) -> None:
    """THE only writer. Same-directory temp file → fsync → ``os.replace``.

    Same directory because ``os.replace`` is atomic only within one volume (and
    on Windows a cross-volume rename raises outright); fsync because otherwise
    the rename can reach the disk before the data and a power loss leaves a
    successfully-renamed zero-length registry.

    The replace is RETRIED on the transient-lock family, which
    ``services/datasets.py``'s ``_rename_with_retry`` documents and this module
    then measured for itself: a browser polling ``GET /api/genesis/jobs/{id}``
    once a second holds a read handle on this file for microseconds at a time,
    and on Windows that is enough for the worker thread's ``os.replace`` to come
    back ``WinError 5``. Those handles outlive nothing; a genuinely held file
    (an editor, a backup agent) outlasts every attempt and still reports
    cleanly, because the last failure propagates unchanged.
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
    serialized = json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_IO_RETRY_ATTEMPTS):
            try:
                os.replace(tmp_path, path)
                break
            except OSError:
                if attempt + 1 >= _IO_RETRY_ATTEMPTS:
                    raise
                time.sleep(_IO_RETRY_DELAY_S)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:  # pragma: no cover - inert leftover
            logger.warning("genesis_jobs: could not remove temp registry %s", tmp_path)
        raise


def _upsert(record: dict[str, Any]) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        jobs = _read_registry()
        jobs[str(record["id"])] = record
        _write_registry(jobs)
    return record


def _update(job_id: str, **updates: Any) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        jobs = _read_registry()
        record = jobs.get(job_id)
        if record is None:
            raise GenesisJobNotFoundError(f"Unknown genesis job: {job_id!r}")
        record.update(updates)
        if "phase" in updates and "progress" not in updates:
            record["progress"] = _PHASE_PROGRESS.get(str(updates["phase"]), record.get("progress", 0.0))
        if updates.get("status") in TERMINAL_STATUSES:
            record["completed_at"] = _now_iso()
        if updates.get("status") == STATUS_RUNNING and not record.get("started_at"):
            record["started_at"] = _now_iso()
        jobs[job_id] = record
        _write_registry(jobs)
    return record


def _update_with_retry(job_id: str, **updates: Any) -> dict[str, Any]:
    """:func:`_update`, retried across the transient-lock family.

    ``_write_registry`` already retries its ``os.replace``; this adds a second,
    coarser retry around the whole read-modify-write for the ONE call that must
    not be lost — the publish-record write, whose failure would otherwise leave
    the workspace's active dataset swapped while the job says it never was.
    """
    last_error: OSError | None = None
    for attempt in range(_IO_RETRY_ATTEMPTS):
        try:
            return _update(job_id, **updates)
        except OSError as exc:
            last_error = exc
            if attempt + 1 < _IO_RETRY_ATTEMPTS:
                time.sleep(_IO_RETRY_DELAY_S)
    assert last_error is not None  # the loop only exits here on a raised OSError
    raise last_error


def get_job(job_id: str) -> dict[str, Any]:
    """One job record from THIS workspace.

    Raises:
        GenesisJobNotFoundError: unknown id, malformed id, or a job belonging to
            another workspace — indistinguishable on purpose.
    """
    if not job_id or not _ID_RE.match(job_id):
        raise GenesisJobNotFoundError(f"Unknown genesis job: {job_id!r}")
    record = _read_registry().get(job_id)
    if record is None:
        raise GenesisJobNotFoundError(f"Unknown genesis job: {job_id!r}")
    return record


def list_jobs() -> list[dict[str, Any]]:
    """This workspace's jobs, newest first. Never raises for an unreadable
    registry — a listing must not 500 over a transient share lock — but the
    failure is logged rather than swallowed, and every WRITE still refuses."""
    try:
        jobs = _read_registry()
    except GenesisRegistryError as exc:
        logger.error("genesis_jobs: %s", exc)
        return []
    return sorted(jobs.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


# ---------------------------------------------------------------------------
# Submitting and running a generation
# ---------------------------------------------------------------------------


def create_genesis_job(
    *,
    osm_path: Path,
    name: str | None = None,
    exclude_service: bool = False,
    source_filename: str | None = None,
    submitted_by: str | None = None,
    demand_input: Path | None = None,
    signals_input: Path | None = None,
) -> dict[str, Any]:
    """Register a generation job WITHOUT starting it.

    The workspace is captured HERE, on the submitting (request) thread, and
    stored on the record — ContextVars do not cross ``threading.Thread``
    boundaries, and ``current_workspace_id()`` raises rather than falling back
    to somebody else's project. Same freeze-at-enqueue discipline
    ``routers/simulations.py``'s ``start_simulation_job`` uses, and for the same
    reason.

    The staged ``.osm`` is COPIED into the job's work directory here, so a
    staging sweep (or a second job reusing the token) cannot change the input
    out from under a running generation. ``demand_input`` and ``signals_input``
    are frozen the same way and for the same reason: what a generation was
    JUDGED on has to still be there when somebody asks six months later why the
    verdict was what it was.

    Split from :func:`start_genesis_job` so that "a job exists" and "a job is
    running" are two separable facts: production always does both, and a caller
    that wants to run one synchronously (a test, a future queue with its own
    scheduler) does not have to race a thread it did not ask for.
    """
    workspace_id = workspaces.current_workspace_id()
    resolved_name = _resolve_name(name, osm_path, source_filename)

    job_id = uuid4().hex[:12]
    work_dir = job_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    frozen_osm = work_dir / f"source{osm_path.suffix.lower() or '.osm'}"
    shutil.copyfile(osm_path, frozen_osm)

    staged_layers: dict[str, dict[str, Any]] = {}
    for kind, source in (
        (side_inputs_module.KIND_DEMAND, demand_input),
        (side_inputs_module.KIND_SIGNALS, signals_input),
    ):
        if source is None:
            continue
        frozen = work_dir / _SIDE_INPUT_STORED_AS[kind]
        shutil.copyfile(source, frozen)
        staged_layers[kind] = {
            "filename": Path(source).name,
            "bytes": frozen.stat().st_size,
            "sha256": _sha256(frozen),
            "stored_as": frozen.name,
        }

    record = {
        "id": job_id,
        "workspace_id": workspace_id,
        "name": resolved_name,
        "status": STATUS_QUEUED,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "submitted_by": submitted_by,
        "options": {"exclude_service": bool(exclude_service)},
        # No absolute server path here. The record is returned verbatim by
        # ``GET /api/genesis/jobs/{id}``, so an absolute path would publish the
        # server's filesystem layout (data root, workspace id, work-dir naming)
        # to every caller — and, on the default workspace, to anonymous ones.
        # Everything this module needs is re-derivable from the job id:
        # :func:`job_dir` and :func:`artifact_path` own that mapping, so nothing
        # has to carry a path around to find its own files.
        "source_osm": {
            "filename": Path(source_filename or osm_path.name).name,
            "bytes": frozen_osm.stat().st_size,
            "sha256": _sha256(frozen_osm),
            "stored_as": frozen_osm.name,
        },
        # ``None`` — not ``{}`` — when nobody supplied any, so "this generation
        # was geometry-only" and "this generation had side inputs that produced
        # nothing" can never read alike.
        "side_inputs": staged_layers or None,
        "network": None,
        "artifact": None,
        "layers": None,
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
    }
    _upsert(record)
    return record


def start_genesis_job(**kwargs: Any) -> dict[str, Any]:
    """:func:`create_genesis_job`, then run it on a daemon worker thread.

    The house pattern ``routers/simulations.py`` uses: the request handler
    returns the accepted record immediately and the client polls. Generation is
    seconds-to-a-minute of pure Python — no COM, no Vissim — so there is nothing
    here that needs the COM lane's serialisation, only its discipline about
    never doing long work inside a request handler.
    """
    record = create_genesis_job(**kwargs)
    threading.Thread(
        target=run_genesis_job,
        args=(record["id"], record["workspace_id"]),
        daemon=True,
    ).start()
    return record


def run_genesis_job(job_id: str, workspace_id: str) -> dict[str, Any]:
    """The job runner body, workspace re-bound explicitly.

    Public and synchronous so tests (and any future queue) can drive one
    generation end-to-end without a thread; ``start_genesis_job`` is the only
    production caller and it always passes the SUBMITTING workspace.
    """
    with workspaces.bind_workspace(workspace_id):
        return _run_genesis_job_locked(job_id)


def _requested_bbox_of(osm_path: Path) -> tuple[float, float, float, float] | None:
    """The window this job's ``.osm`` was FETCHED for, out of its own bytes.

    The requested bbox survives a fetch only as the leading ``<!--
    prism-osm-fetch -->`` comment ``services/osm_fetch.py`` writes into the
    frozen ``source.osm``. Until this function existed nothing read it during a
    run, so the generator had no idea what study area it had been asked for and
    fell back to the hull of the geometry Overpass sent — which is set by the
    longest way tail hanging out of the area, not by the area. See
    ``connectivity._boundary_rectangle`` for what that cost.

    Read from the FILE rather than from the job record on purpose: the record
    crosses the HTTP boundary and carries no region, and the file is the thing
    the artifact was actually built from. A file with no provenance comment — an
    upload — yields ``None``, and the build behaves exactly as it did before.
    Never raises: an unreadable sidecar fact must not fail a generation that the
    ``.osm`` itself can still support.
    """
    # Thin view over ``_requested_region_of`` — ONE reader owns the file I/O and
    # the OSError posture, so the two cannot drift apart (they briefly did: this
    # function's body duplicated the read until the 2026-08-13 polygon review
    # flagged it orphaned).
    return _requested_region_of(osm_path)[0]


def _requested_region_of(
    osm_path: Path,
) -> tuple[
    tuple[float, float, float, float] | None, tuple[tuple[float, float], ...] | None
]:
    """The rectangle AND the lasso this job's ``.osm`` was fetched for.

    One read of the file for both, because the provenance comment is read out of
    the whole document and doing it twice would double the I/O of every job for a
    fact that arrives in the same JSON blob. The rectangle is the load-bearing
    one (it moves geometry, through the trim and the boundary ladder); the ring is
    carried for the LABEL and the artifact metadata — see
    ``osm.OsmExtract.requested_polygon``.
    """
    # Function-local: ``services.osm_fetch`` imports THIS module at module level
    # (it stages fetched extracts through ``stage_osm``), so a top-level import
    # here would be a cycle.
    from services.osm_fetch import read_provenance  # noqa: PLC0415

    try:
        provenance = read_provenance(osm_path.read_bytes())
    except OSError:  # pragma: no cover - the parse below reports the same file
        return None, None
    return (
        requested_bbox_from_provenance(provenance),
        requested_polygon_from_provenance(provenance),
    )


def _requested_region_summary(
    requested_bbox: tuple[float, float, float, float] | None,
    requested_polygon: tuple[tuple[float, float], ...] | None,
) -> dict[str, Any] | None:
    """WHAT WAS ASKED FOR, small enough to live on the job record.

    Added by U-1-quick (docs/ux-backlog.md): the 생성 이력 list showed a name and
    a date, which cannot tell two generations of the same city apart. The region
    is already read from the frozen ``.osm``'s provenance on every run
    (:func:`_requested_region_of`), so the only thing missing was writing it
    down — this function is the shape it is written down in, and the record then
    crosses the HTTP boundary through ``GET /jobs`` like every other field.

    ``None`` — the whole value, not an empty dict — when the source document
    carries no fetch provenance at all, which is what a hand-uploaded ``.osm``
    looks like. That is a MEASUREMENT (the file was read and said nothing about
    a region), and it is deliberately distinguishable from a record written
    before this field existed, which has no such key at all. The client turns
    the two into different words and invents a region for neither
    (``lib/genesisArtifacts.ts``'s ``requestedRegionLabel``).

    The bbox keeps the provenance's own compass-word shape rather than a
    4-tuple: this module already carries a warning about how easily a
    ``(lat, lon, lat, lon)`` tuple transposes into a study area rotated onto its
    side (:func:`services.genesis.osm.requested_bbox_from_provenance`), and a
    dict cannot be transposed by a reader who guesses the order wrong.

    The polygon is a COUNT, never the ring. The ring is already in the
    artifact's own metadata (``*.genesis.json``'s ``boundary``), a job record is
    read on every listing, and a 40-vertex lasso repeated across a page of
    history rows is payload nobody asked for.
    """
    if requested_bbox is None and requested_polygon is None:
        return None
    summary: dict[str, Any] = {"bbox": None, "polygon_vertices": None}
    if requested_bbox is not None:
        south, west, north, east = requested_bbox
        summary["bbox"] = {
            "south": south,
            "west": west,
            "north": north,
            "east": east,
        }
    if requested_polygon is not None:
        summary["polygon_vertices"] = len(requested_polygon)
    return summary


def _run_genesis_job_locked(job_id: str) -> dict[str, Any]:
    record = get_job(job_id)
    try:
        # Re-derived from the job id rather than read off the record: the
        # record crosses the HTTP boundary and must not carry server paths.
        osm_path = job_dir(job_id) / str(record["source_osm"]["stored_as"])
        exclude_service = bool((record.get("options") or {}).get("exclude_service"))

        requested_bbox, requested_polygon = _requested_region_of(osm_path)
        # Written in the SAME registry write that flips the job to running.
        # The region costs no new I/O — ``_requested_region_of`` was already
        # being called here, because ``parse_osm_file`` below needs the bbox —
        # so persisting it is one dict on a write this line was making anyway,
        # rather than a second ``_update`` per generation.
        _update(
            job_id,
            status=STATUS_RUNNING,
            phase=PHASE_READING_OSM,
            requested_region=_requested_region_summary(requested_bbox, requested_polygon),
        )
        extract = parse_osm_file(
            osm_path,
            exclude_service=exclude_service,
            requested_bbox=requested_bbox,
            requested_polygon=requested_polygon,
        )
        if extract.refused:
            raise GenesisRefused(
                "osm_unreadable", [refusal.message for refusal in extract.refusals]
            )

        # Unit #42 -- the ONE declarative parameter a dead-end exclusion job
        # carries, applied HERE and nowhere else: after the faithful parse of
        # the source (which is evidence and is never edited), before the draft.
        # ``without_ways`` leaves a named skip per way, so the exclusion travels
        # into the census and the compliance report as a LEDGER ENTRY carrying
        # who confirmed it and why -- B5-1's rule for an exclusion, applied to a
        # human's confirmation instead of the isolation pass's own measurement.
        extract = _apply_declared_exclusions(job_id, record, extract)

        _update(job_id, phase=PHASE_DRAFTING)
        draft = build_draft(extract, name=str(record["name"]))
        if draft.refused:
            raise GenesisRefused("draft_refused", list(draft.refusals))

        # The optional layers, when the caller supplied their data. Everything
        # about them — including the decision to refuse the whole generation
        # rather than emit a network missing a layer somebody asked for — is in
        # :func:`_compose_side_layers`.
        layers = _compose_side_layers(job_id, record, draft)

        _update(job_id, phase=PHASE_EMITTING)
        work_dir = job_dir(job_id)
        name = str(record["name"])
        inpx_path = emit_inpx(
            draft,
            work_dir / f"{name}.inpx",
            layers.signals,
            layers.demand,
            layers.fleet,
        )
        compliance_path = work_dir / f"{name}.compliance.md"
        compliance_path.write_text(draft.render_compliance("ko"), encoding="utf-8", newline="")
        census_path = work_dir / f"{name}.genesis.json"
        census_path.write_text(
            json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="",
        )

        counts = draft.compliance.counts()
        record = _update(
            job_id,
            phase=PHASE_GATING,
            network={
                "links": len(draft.links),
                "connectors": len(draft.connectors),
                "nodes": len(draft.nodes),
                "compliance": counts,
            },
            artifact={
                "inpx": inpx_path.name,
                "compliance_md": compliance_path.name,
                "genesis_json": census_path.name,
                "sha256": _sha256(inpx_path),
                "bytes": inpx_path.stat().st_size,
            },
        )
        return gate_and_publish(job_id)
    except GenesisPublicationRecordError as exc:
        # The dataset IS live; only its record write failed. Repair the record
        # to the TRUE published/provisional state rather than falling through to
        # the generic handler below, which would mislabel a live dataset
        # ``error`` / "no artifact exists". One more attempt (a lock that
        # failed a moment ago is very likely free now); if even that fails, the
        # last-known non-terminal record is returned unchanged — never a status
        # that contradicts what is live.
        logger.critical(
            "genesis_jobs: repairing job %s — its dataset is registered but the publish record "
            "did not land on the first pass.",
            job_id,
        )
        try:
            return _update_with_retry(
                job_id,
                status=exc.status,
                phase=PHASE_DONE,
                progress=1.0,
                gate=exc.gate,
                publication=exc.publication,
                revocation=None,
                error=None,
            )
        except Exception:  # noqa: BLE001 - registry still unwritable; do not lie
            logger.critical(
                "genesis_jobs: job %s could not be repaired (registry still unwritable). Its "
                "active dataset IS live; the record must be reconciled by hand.",
                job_id,
                exc_info=True,
            )
            return get_job(job_id)
    except GenesisRefused as exc:
        logger.info("genesis_jobs: job %s produced no network (%s)", job_id, exc.code)
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={"code": exc.code, "reasons": exc.reasons},
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - a job failure is data, not a crash
        logger.exception("genesis_jobs: job %s failed", job_id)
        # A step that already recorded WHY it failed keeps its own reason: the
        # publish step names ``publish_failed`` and preserves the gate verdict,
        # and overwriting that with a generic ``generation_failed`` here would
        # throw away the more specific answer on the way past.
        try:
            already = get_job(job_id)
        except GenesisJobError:  # pragma: no cover - the record was just read
            already = {}
        if (already.get("refusal") or {}).get("code"):
            return already
        code = "emit_refused" if isinstance(exc, EmitRefused) else "generation_failed"
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={"code": code, "reasons": [f"{type(exc).__name__}: {exc}"]},
            error=f"{type(exc).__name__}: {exc}",
        )


def _apply_declared_exclusions(
    job_id: str, record: Mapping[str, Any], extract: "OsmExtract"
) -> "OsmExtract":
    """The unit #42 exclusion step: remove every way this job's record DECLARES
    excluded, each leaving a named skip whose detail is the whole attribution.

    A no-op returning the SAME object for every ordinary generation — a job with
    no ``excluded_ways`` key produces a byte-identical artifact to the one it
    produced before this function existed, which is the only acceptable cost of
    adding a step to the shared runner.

    Refuses BY NAME rather than proceeding when a declared way is not in the
    parsed extract (``dead_end_excluded_way_not_in_source``). That state should
    be unreachable — the create path verified the way against the same source's
    own index, and this pipeline is deterministic — but "the exclusion I
    declared did not apply" is precisely the silent difference between what a
    job claims and what it did, and this package exists to refuse those. A
    refusal here leaves the job in ``error`` with the code on its record; it
    does not mint an artifact whose lineage would be a lie.
    """
    declared = list(record.get("excluded_ways") or [])
    if not declared:
        return extract
    by_id: dict[int, Mapping[str, Any]] = {}
    for entry in declared:
        try:
            by_id[int(entry["way_id"])] = entry
        except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - record is ours
            raise GenesisRefused(
                "dead_end_excluded_way_not_in_source",
                [f"An excluded_ways entry names no readable way id: {entry!r} [{exc}]"],
            ) from exc
    present = {way.id for way in extract.ways}
    missing = sorted(way_id for way_id in by_id if way_id not in present)
    if missing:
        raise GenesisRefused(
            "dead_end_excluded_way_not_in_source",
            [
                f"Job {job_id} declares way(s) {missing} excluded as operator-confirmed "
                "real-world dead ends, but this parse of its source holds no drivable way with "
                "those ids. Nothing was generated: an artifact whose lineage claims an "
                "exclusion that never applied would be a record of something that did not "
                "happen."
            ],
        )

    def detail_for(way_id: int) -> str:
        entry = by_id[way_id]
        return (
            f"operator-confirmed real-world dead end: {entry.get('confirmed_by')} confirmed at "
            f"{entry.get('confirmed_at')} against finding {entry.get('finding_code')} "
            f"({entry.get('subject')!r}); basis (their own words, verbatim): "
            f"\"{entry.get('basis')}\""
        )

    logger.info(
        "genesis_jobs: job %s excludes %d operator-confirmed dead-end way(s): %s",
        job_id,
        len(by_id),
        sorted(by_id),
    )
    return extract.without_ways(
        sorted(by_id),
        reason=SKIP_CONFIRMED_REAL_WORLD_DEAD_END,
        detail_for=detail_for,
    )


def _compose_side_layers(
    job_id: str, record: dict[str, Any], draft: DraftNetwork
) -> ComposedLayers:
    """The demand/fleet/signal layers for this job, or :data:`NO_LAYERS`.

    Three things happen here and nowhere else:

    1. **Nothing at all when nobody asked.** No side inputs on the record means
       this returns the empty composition, ``layers.json`` is not written, the
       record's ``layers`` stays ``None`` and the emitter is handed the same
       three ``None``s it always was. A geometry-only generation is unchanged
       down to the byte, which ``tests/test_genesis_job_layers.py`` pins against
       a file generated by the pre-existing path.
    2. **The findings are written before the decision.** ``layers.json`` lands
       whether the composition passed or refused, exactly as the gate's own
       verdict does: a refusal is a deliverable, and a caller whose counts were
       rejected needs the coverage tables that say which approach joined and
       which did not.
    3. **A refusal refuses the GENERATION.** Never a partial emission. The
       modules are all-or-nothing individually; this makes the SET of them
       all-or-nothing, so "I uploaded counts and got a network with no demand"
       cannot happen.
    """
    staged = record.get("side_inputs") or {}
    if not staged:
        return NO_LAYERS

    work_dir = job_dir(job_id)
    documents: dict[str, Any] = {}
    for kind, entry in staged.items():
        path = work_dir / str((entry or {}).get("stored_as") or "")
        if not path.is_file():  # pragma: no cover - written at enqueue
            raise GenesisRefused(
                "side_inputs_missing",
                [
                    f"The frozen {kind} side input is no longer in this job's work directory; "
                    "the generation cannot be repeated on the data it was submitted with."
                ],
            )
        try:
            documents[kind] = side_inputs_module.load_side_input(kind, path)
        except side_inputs_module.SideInputRefused as exc:
            raise GenesisRefused(exc.code, exc.reasons) from exc

    layers = side_inputs_module.compose_from_documents(
        draft,
        demand=documents.get(side_inputs_module.KIND_DEMAND),
        signals=documents.get(side_inputs_module.KIND_SIGNALS),
        job_id=job_id,
    )

    (work_dir / LAYERS_JSON_FILENAME).write_text(
        json.dumps(layers.as_dict("ko"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    _update(job_id, layers=layers.summary())

    if not layers.ok:
        logger.info(
            "genesis_jobs: job %s refused its side inputs (%s)",
            job_id,
            sorted(set(layers.issue_codes(IssueSeverity.REFUSE))),
        )
        raise GenesisRefused("side_inputs_refused", layers.refusal_reasons("ko"))
    return layers


# ---------------------------------------------------------------------------
# THE exit door — A4. Nothing below this line may be bypassed.
# ---------------------------------------------------------------------------


def _archive_previous_gate(work_dir: Path) -> str | None:
    """Preserve the verdict bytes a re-gate is about to overwrite (unit #33).

    Returns the archived copy's path RELATIVE to the job directory (so the job
    record can name it without leaking a server path), or ``None`` when there
    was no previous verdict — a first gate archives nothing, and that absence is
    the honest answer, not an empty file.

    See :data:`GATE_HISTORY_DIRNAME` for why the archive is a directory rather
    than ``gate.json.N`` siblings. Best-effort by design: a job directory that
    refuses this copy must not stop the gate from running, because refusing to
    re-gate would leave the job carrying a verdict that new evidence has already
    contradicted. The failure is logged, and the record then says the archive is
    absent rather than claiming one exists.
    """
    current = work_dir / GATE_JSON_FILENAME
    if not current.is_file():
        return None
    try:
        history = work_dir / GATE_HISTORY_DIRNAME
        history.mkdir(parents=True, exist_ok=True)
        index = 1
        while (history / f"gate.{index:04d}.json").exists():
            index += 1
        target = history / f"gate.{index:04d}.json"
        # copyfile, not a re-dump of a parsed GateResult: the point is the BYTES
        # somebody may already have downloaded and quoted.
        shutil.copyfile(current, target)
    except OSError:
        logger.exception(
            "genesis_jobs: could not archive the previous verdict in %s", work_dir
        )
        return None
    return f"{GATE_HISTORY_DIRNAME}/{target.name}"


def gate_and_publish(
    job_id: str,
    *,
    live_oracle: LoadOracle | None = None,
    live_oracle_gap: str | None = None,
    attestation: dict[str, Any] | None = None,
    regate_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the R1.5 gate over this job's artifact and decide what happens to it.

    **This is the only path from a generated artifact to a workspace dataset**,
    and the reason it can be called that is structural rather than documentary:

    * it takes no verdict, no state and no "already gated" flag — the verdict is
      COMPUTED here, on the artifact's current bytes, by ``run_gate``;
    * the full result and its ko report are written beside the artifact
      unconditionally, before any branch, so a refusal is as durable as a pass;
    * :func:`_register_as_active_dataset` is called from exactly one place in
      this module — the ``verdict is not REFUSED`` branch below — and
      ``tests/test_genesis_jobs.py`` pins that as a property of the source.

    ``live_oracle`` is V1's evidence, produced ONLY by
    ``LoadOracle.from_probe_state`` (see :func:`attach_live_check`). Passing
    ``None`` is the normal case for a fresh generation and is what makes the
    result ``provisional``: an offline run can never report an unqualified pass,
    which is R1.5's design rule and not an omission.

    ``attestation`` names the human whose claim the ``live_oracle`` rests on
    (:func:`attach_live_check`). It is carried into the gate summary rather than
    inferred there, so no reader of a verdict has to guess whether V1 went green
    because the app watched Vissim or because somebody said it did. Its
    ``basis`` field is what says which of the two this is
    (:data:`ATTESTATION_BASIS` vs :data:`LIVE_CHECK_BASIS`); omitted, it reads as
    an operator's attestation, which is the conservative answer.

    ``regate_source`` names what CAUSED this re-gate when something did — the
    live-check job whose probe produced the evidence, and when. It is recorded
    beside the archived previous verdict (:func:`_archive_previous_gate`) so a
    reader of a changed verdict can always answer "what changed it, and what did
    it say before". ``None`` for a first gate, which has neither.

    **Concurrent re-gates on the same job are serialized (unit #33 F1).** The
    live-check job path (:func:`run_live_check_job`) and :func:`attach_live_check`
    both re-gate their target through this same function, and nothing upstream
    stops both from reaching it for the same job at once — a double-click across
    two tabs is enough. :func:`_gate_lock` holds one job's whole
    read-old-gate → archive → write-new-gate → update-registry span as a single
    atomic unit, so the two racers' verdicts always end up ordered (one
    superseding the other, both preserved) rather than one silently vanishing
    when both writers picked the same old verdict to archive.
    """
    record = get_job(job_id)
    artifact = record.get("artifact")
    if not artifact:
        raise GenesisJobError(
            f"Job {job_id} has no artifact to gate — it never produced a network."
        )

    with _gate_lock(job_id):
        # Re-read under the lock rather than trusting the snapshot above: a
        # caller that queued behind another racer must act on what the WINNER
        # left behind (its fresh ``publication``, most of all — see the REFUSED
        # branch below), never on what the record said before it waited.
        record = get_job(job_id)
        artifact = record.get("artifact")
        if not artifact:  # pragma: no cover - defensive; the same check just ran
            raise GenesisJobError(
                f"Job {job_id} has no artifact to gate — it never produced a network."
            )

        work_dir = job_dir(job_id)
        inpx_path = work_dir / str(artifact["inpx"])
        census_path = work_dir / str(artifact["genesis_json"])
        metadata: dict[str, Any] | None = None
        if census_path.is_file():
            try:
                metadata = json.loads(census_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # pragma: no cover - we wrote this file
                metadata = None

        # The gap is resolved ONCE, here, and the same value reaches both the gate
        # and the record — so "why is V1 grey" reads the same in the check's own gap
        # and in the job summary the list view shows.
        effective_gap = live_oracle_gap
        if live_oracle is None and not effective_gap:
            effective_gap = "no_live_check_evidence_attached"

        _update(job_id, phase=PHASE_GATING)
        result = run_gate(
            inpx_path,
            draft_metadata=metadata,
            load_oracle=live_oracle,
            load_oracle_gap=effective_gap,
        )

        # A verdict that is about to be replaced is preserved FIRST, whatever the
        # new one turns out to be (unit #33). Nothing downstream may overwrite the
        # evidence a reader was already shown.
        archived_gate = _archive_previous_gate(work_dir)

        # Stored BEFORE the branch: a refusal is a deliverable. The .inpx is
        # re-hashed here rather than trusting the record, so the verdict on disk is
        # provably about the bytes that are there now.
        (work_dir / GATE_JSON_FILENAME).write_text(
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )
        (work_dir / GATE_REPORT_FILENAME).write_text(
            render_report(result, language="ko"), encoding="utf-8", newline=""
        )

        gate_summary = _gate_summary(
            result,
            live_oracle=live_oracle,
            live_oracle_gap=effective_gap,
            attestation=attestation,
            regate=(
                {**(regate_source or {}), "previous_gate_archived": archived_gate}
                if (regate_source or archived_gate)
                else None
            ),
        )

        if result.verdict is GateVerdict.REFUSED:
            previous = record.get("publication")
            revocation = _revoke_publication(record) if previous else None
            return _update(
                job_id,
                status=STATUS_REFUSED,
                phase=PHASE_DONE,
                progress=1.0,
                gate=gate_summary,
                publication=None,
                revocation=revocation,
                error=None,
            )

        _update(job_id, phase=PHASE_PUBLISHING)
        try:
            publication = _register_as_active_dataset(
                record, inpx_path, draft_metadata=metadata
            )
        except Exception as exc:  # noqa: BLE001 - see below; recorded, then re-raised
            # A storage refusal here is NORMAL operation on Windows, not an exotic
            # error: ``services/datasets.py`` documents that anything holding
            # ``uploads/current`` open (Vissim, a results reader, an explorer
            # window) makes the promote fail — and it guarantees the previous
            # dataset survives. What must not happen is the job being left at
            # ``running``/``publishing`` forever with its verdict lost, so the gate
            # result is written to the record BEFORE the exception continues on to
            # the runner (which marks the job) or to the HTTP caller (409).
            logger.error(
                "genesis_jobs: job %s passed the gate but could not be registered as the "
                "project's dataset (%s: %s). The previous dataset is unchanged.",
                job_id,
                type(exc).__name__,
                exc,
            )
            _update(
                job_id,
                status=STATUS_ERROR,
                phase=PHASE_DONE,
                progress=1.0,
                gate=gate_summary,
                publication=None,
                refusal={
                    "code": "publish_failed",
                    "reasons": [f"{type(exc).__name__}: {exc}"],
                },
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        status = (
            STATUS_PUBLISHED if result.verdict is GateVerdict.PASS else STATUS_PROVISIONAL
        )
        try:
            return _update_with_retry(
                job_id,
                status=status,
                phase=PHASE_DONE,
                progress=1.0,
                gate=gate_summary,
                publication=publication,
                # Cleared rather than left behind: a job that is published NOW must
                # not still carry the revocation notice from an earlier refused
                # re-gate, or the record contradicts itself and a reader has to
                # guess which half is current.
                revocation=None,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - the dataset is already live; see below
            # The register above SUCCEEDED: the workspace's active dataset IS this
            # artifact right now. Only the registry write that records that fact
            # failed (a transient Windows lock past its retries). Falling through to
            # the runner's generic handler would stamp ``status=error`` /
            # ``generation_failed`` — whose contract is "no artifact exists" — over a
            # job whose network is live, and lose the publication record forever.
            # Raise a DISTINCT error the runner repairs instead of mislabelling, so
            # the recorded status can never contradict what is actually live.
            logger.critical(
                "genesis_jobs: job %s registered its dataset but the publication record could not "
                "be written (%s: %s). The workspace's active dataset IS this artifact; the job will "
                "be repaired rather than marked failed.",
                job_id,
                type(exc).__name__,
                exc,
            )
            raise GenesisPublicationRecordError(
                job_id, status=status, gate=gate_summary, publication=publication, cause=exc
            ) from exc


def _dataset_georeference(draft_metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """The transform that places THIS network on the earth, or ``None``.

    U-1-map (docs/ux-backlog.md, elaboration item #7). Read straight out of the
    draft's own ``metadata.projection`` — the origin ``services/genesis/draft.py``
    recorded when it projected the OSM degrees into metres — and handed to
    ``datasets.set_active_dataset_georeference`` so ``routers/geojson.py`` can
    invert THIS dataset's metres instead of applying the SouthSt Helmert
    constants to every dataset alike.

    ``None`` whenever the origin is not both present and numeric. That is not a
    defensive shrug: the caller CLEARS the record on ``None``, so a generation
    whose draft carries no origin leaves the dataset with no transform (the map
    then reports it honestly) rather than inheriting the previous dataset's —
    which would draw this network in the last one's place.

    The units are stated in the record (``kind``) rather than assumed by the
    reader, and no radius is copied: they are derived from the origin, and
    ``LocalTangentPlane`` recomputes them from it on the other side.
    """
    from services.genesis.project import is_valid_origin_deg

    projection = ((draft_metadata or {}).get("metadata") or {}).get("projection") or {}
    lat = projection.get("origin_lat_deg")
    lon = projection.get("origin_lon_deg")
    # The SAME range check `services.genesis.geojson.plane_for_draft` applies on
    # the read side, applied here on the write side: a swapped-but-numeric pair
    # recorded onto a dataset would mirror the dashboard map onto the far side
    # of the planet, and it is cheaper to refuse to record it than to notice
    # later. `None` CLEARS the record, so the dataset ends up honestly
    # untransformed rather than inheriting the previous one's plane.
    if not is_valid_origin_deg(lat, lon):
        return None
    return {
        "kind": str(projection.get("kind") or ""),
        "datum": str(projection.get("datum") or "WGS84"),
        "origin_lat_deg": float(lat),
        "origin_lon_deg": float(lon),
        "source": "genesis_draft",
    }


def _register_as_active_dataset(
    record: dict[str, Any],
    inpx_path: Path,
    *,
    draft_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make this artifact the workspace's active Vissim dataset.

    Private, and called from exactly ONE place — :func:`gate_and_publish`, after
    the REFUSED branch has already returned. That single call site IS A4: there
    is no other route from ``services/genesis`` output to a registered dataset,
    so no future code path can grow one by forgetting to gate.

    Reuses ``services.datasets.save_active_dataset``, which is the same
    stage-then-promote transaction a browser upload takes (unit 1.4) — so a
    generated network is registered exactly as carefully as an uploaded one, and
    a failure mid-promote leaves the previous dataset byte-identical. What the
    generation REPLACED is recorded, because "my model disappeared" is otherwise
    an unanswerable support question.

    ``draft_metadata`` is the census :func:`gate_and_publish` already read (the
    same dict the gate judged against, never re-read here — two reads of one
    file is two answers waiting to disagree). Its projection origin is written
    to the dataset AFTER the promote, so the transform can never be recorded for
    a dataset that failed to land. See :func:`_dataset_georeference`.
    """
    previous = datasets.get_active_dataset()
    # ONE call, holding ONE lock across the promote and the georeference write.
    # These used to be two separately-locked calls, and the gap between them was
    # a real window: a second registration landing there left ITS bytes under
    # THIS generation's projection origin, both writes reporting success. See
    # ``datasets.save_active_dataset_with_georeference``.
    stored = datasets.save_active_dataset_with_georeference(
        f"{record['name']}.inpx",
        inpx_path.read_bytes(),
        _dataset_georeference(draft_metadata),
    )
    return {
        "published_at": _now_iso(),
        "dataset_filename": stored.get("filename"),
        "dataset_revision": stored.get("revision"),
        "artifact_sha256": _sha256(inpx_path),
        "replaced": {
            "source": previous.get("source"),
            "filename": previous.get("filename"),
            "revision": previous.get("revision"),
        },
    }


def _revoke_publication(record: dict[str, Any]) -> dict[str, Any]:
    """Undo a publication whose artifact the gate now refuses.

    Only when the workspace's active dataset is still byte-identical to this
    job's artifact. Live evidence arriving late can turn V1 from NOT_CHECKABLE
    into FAIL — a real Vissim refused to open the very file we published — and
    leaving that network registered would mean a refuse-tier fault sitting in a
    consultant's project with a green-ish job beside it.

    What it must never do is delete somebody ELSE's dataset to tidy up our own
    bookkeeping: if the active dataset has since been replaced (by an upload or
    by another generation), the revocation is RECORDED and nothing on disk is
    touched. The identity check and the delete are ONE atomic step —
    ``datasets.reset_active_dataset_if_sha`` re-checks the active hash under the
    same per-workspace upload lock the delete runs under — so a concurrent
    upload landing between "still ours?" and "delete" can no longer make us
    destroy a dataset that was never this artifact. Reported either way: a
    revocation nobody can see is the silent drop this project's operating
    principles forbid.
    """
    publication = record.get("publication") or {}
    expected_sha = str(publication.get("artifact_sha256") or "")
    if not expected_sha:
        return {
            "revoked_at": _now_iso(),
            "removed_from_workspace": False,
            "reason": "gate_refused_after_live_evidence",
            "detail": (
                "This job carries no published-artifact hash, so there is nothing that can be "
                "safely identified and removed. The network this job published is refused and "
                "must not be used."
            ),
        }
    try:
        outcome = datasets.reset_active_dataset_if_sha(expected_sha)
    except Exception as exc:  # noqa: BLE001 - report, never mask the refusal
        logger.error(
            "genesis_jobs: the gate now refuses job %s but its published dataset could not "
            "be removed (%s: %s). The network is STILL registered and must be replaced by "
            "hand.",
            record.get("id"),
            type(exc).__name__,
            exc,
        )
        return {
            "revoked_at": _now_iso(),
            "removed_from_workspace": False,
            "reason": "gate_refused_after_live_evidence",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    if outcome.get("removed"):
        logger.warning(
            "genesis_jobs: job %s was refused on re-gate; its published dataset was removed "
            "from the workspace.",
            record.get("id"),
        )
        return {
            "revoked_at": _now_iso(),
            "removed_from_workspace": True,
            "reason": "gate_refused_after_live_evidence",
        }
    return {
        "revoked_at": _now_iso(),
        "removed_from_workspace": False,
        "reason": "gate_refused_after_live_evidence",
        "detail": (
            "The project's active dataset is no longer this artifact, so nothing was deleted. "
            "The network this job published is refused and must not be used."
        ),
    }


# ---------------------------------------------------------------------------
# FIX JOBS — roadmap B5-2 stage 2. "거부는 진단" (a refusal IS the diagnosis):
# a fix job clones a REFUSED generation's artifact, applies ONE approved verb
# on the job-queue thread pattern, and re-gates the clone as a NEW SIBLING
# job whose ``derived_from`` points at the parent. Nothing here may write a
# single byte under the parent's own work dir — the parent's artifacts are the
# "before" evidence of the diagnosis this fix is answering, and overwriting
# them in place would destroy the very thing a re-gate is supposed to compare
# against.
#
# The gate stays the only exit door: :func:`run_genesis_fix_job` ends by
# calling the UNMODIFIED :func:`gate_and_publish` on the sibling job, exactly
# as a fresh generation does, so a fix that happens to repair every refuse-tier
# fault publishes -- and one that does not stays refused -- through the same
# structural gate A4 already guarantees. This section imports nothing new into
# THAT guarantee; it only ever gets to CALL it.
#
# --- Fix round (adversarial pass, roadmap B5-2 stage 3 hardening) ----------
# An executable exploit proved that approving a fix WITHOUT re-deriving its
# proposal from real, server-side evidence lets a caller (a) mint a fix job
# for a finding the parent's own verdict never made, (b) overwrite a
# MEASURED entrance's volume by hand-crafting params, and (c) apply params
# that address a DIFFERENT object than the one the named finding diagnosed.
# :func:`create_genesis_fix_job` below re-runs :mod:`services.agent.genesis_fixes`
# against THIS evidence before it ever mints a job directory, so every
# caller — HTTP or otherwise — is covered, not merely a router-side check.
# ---------------------------------------------------------------------------


def iter_stored_gate_findings(
    raw_gate: dict[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """``(check_id, origin, finding)`` for EVERY finding AND warning in a raw,
    stored ``gate.json`` -- ``origin`` is ``"findings"`` or ``"warnings"``.

    The ONE walk both ``routers/genesis.py``'s fixes-listing route and this
    module's own finding lookup share, so they can never disagree about what
    counts as "a finding in the verdict". Both a check's ``findings`` and its
    ``warnings`` are walked: V6 rides its own warn clause as ``warnings`` on
    a refuse-tier check (``services.genesis.gate``'s own module docstring),
    and a code living there is still a finding a fix job must answer for.

    Each ``finding`` is a plain dict (``code``/``subject``/``context``) —
    the shape ``services.agent.genesis_fixes`` duck-types, never a
    ``services.genesis.gate.GateFinding`` pydantic instance (this module does
    not import ``gate.py``'s model for this).
    """
    for check in raw_gate.get("checks", []) or []:
        check_id = str(check.get("id") or "")
        for origin in ("findings", "warnings"):
            for raw_finding in check.get(origin, []) or []:
                yield (
                    check_id,
                    origin,
                    {
                        "code": str(raw_finding.get("code") or ""),
                        "subject": str(raw_finding.get("subject") or ""),
                        "context": dict(raw_finding.get("context") or {}),
                    },
                )


def _finding_in_stored_gate(
    raw_gate: dict[str, Any], finding_code: str, subject: str | None = None
) -> dict[str, Any] | None:
    """The finding (or warning) in a raw, stored ``gate.json`` that
    ``(finding_code, subject)`` addresses -- or ``None`` when the parent's
    own verdict never made this diagnosis at all.

    ``subject`` given: the exact ``(code, subject)`` match -- the same pair
    ``gate.py``'s own deterministic ordering already treats as a finding's
    identity (``CheckResult.build`` sorts findings by ``(code, subject)``),
    so EVERY finding in a verdict is individually addressable, including the
    second of two objects sharing one finding code (e.g. two independent
    zero-volume vehicle inputs -- B5-2's own recorded limit, closed by unit
    #30).

    ``subject`` omitted (a pre-unit-#30 caller): the match by code alone,
    but ONLY when the code appears exactly once in the verdict. When it
    appears MORE than once, raises :class:`GenesisRefused`
    ``genesis_fix_finding_ambiguous_without_subject`` naming every candidate
    subject rather than silently resolving to the first match -- a fix aimed
    at the second object landing on the first's identity is exactly the
    quiet wrongness this codebase exists to refuse.
    """
    matches = [
        finding
        for _check_id, _origin, finding in iter_stored_gate_findings(raw_gate)
        if finding["code"] == finding_code
    ]
    if subject is not None:
        for finding in matches:
            if finding["subject"] == subject:
                return finding
        return None
    if len(matches) > 1:
        candidates = ", ".join(repr(finding["subject"]) for finding in matches)
        raise GenesisRefused(
            "genesis_fix_finding_ambiguous_without_subject",
            [
                f"Finding code {finding_code!r} appears {len(matches)} times in this verdict, "
                f"once per subject: {candidates}. Name the subject this fix addresses -- "
                "silently resolving to the first match would let a fix aimed at one object "
                "land on another's identity."
            ],
        )
    return matches[0] if matches else None


def job_draft_or_none(job_id: str) -> "DraftNetwork | None":
    """This job's own ``*.genesis.json`` census, parsed as a real
    ``DraftNetwork`` -- or ``None`` when it is absent, unreadable, or does
    not validate (a fix job's OWN census is never written — see
    :func:`create_genesis_fix_job`'s docstring — and a hand-seeded test
    fixture's census is deliberately minimal).

    Never raises: an unreadable/absent census means a caller derives LESS
    evidence, never that it fails outright. The genuine "job unknown" case is
    caught by the caller before this is ever reached. Shared by BOTH the
    fixes-listing route and :func:`create_genesis_fix_job`'s own
    re-derivation, so a network's own recorded geometry (V9's link length) is
    read the SAME way in both places.
    """
    try:
        path = artifact_path(job_id, "genesis_json")
    except GenesisJobNotFoundError:
        return None
    try:
        return DraftNetwork.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def derive_v6_node_evidence(link_id: str) -> dict[str, Any] | None:
    """Real observed-count evidence for ONE vehicle-input LINK, from THIS
    workspace's ingested observation sets (:mod:`services.observations`) --
    unit D4's own join (``services.agent.fixes.movement_from_link`` /
    ``canonical_link_id``), reused directly rather than reimplemented,
    applied against each set's RAW per-site movement rows
    (``observations.get_observation_set(...)['aggregates'][period][site]
    ['movements']``) rather than through ``observations.observed_side``,
    which additionally needs a site->Vissim-node map genesis has no
    equivalent of — a generated network carries no "observation site"
    concept at all (that join exists only for the counts-workbook DEMAND
    side input, a different pipeline entirely). D4's own question ("was
    THIS link's volume ever measured") does not need a node identity, only a
    FROM-link match, so this scans every set/period/site directly.

    Returns the FIRST matching site's row in the RAW ``observed_side``-node
    shape (``services.agent.fixes.node_evidence_from_real_counts``'s OWN
    expected input — the caller normalizes it, this function does not), or
    ``None`` when no ingested set carries a movement whose FROM-link is this
    one.

    **EVIDENCE SOURCE / KNOWN GAP, not fixed here:** every row this store
    holds is keyed ``"<leg>|<turn>"`` (``services.observations.movement_id``
    — always, unconditionally), never a Vissim movement OBJECT_ID, so
    ``movement_from_link`` can never match a row sourced from THIS store
    today — ``fixes.entrance_measurement``'s own docstring names the missing
    link<->leg edge as a follow-up (``ENTRANCE_NO_LINK_KEYED_ROWS``). This
    function is wired for real against the real store (not stubbed) so the
    day that edge exists, evidence starts flowing with no caller-side
    change; until then it always returns ``None`` for genuinely-ingested
    data, which is the CORRECT "no observations joinable -> UNVERIFIED
    stands" answer, not a bug. A module-level function (not a closure), on
    purpose, so a test can monkeypatch it directly to prove the REFUSAL
    WIRING downstream of a measured entrance without needing the store's own
    gap closed first — the same seam pattern this test suite already uses
    for COM (``_build_fix_edit_session``).
    """
    from services.agent.fixes import canonical_link_id, movement_from_link  # noqa: PLC0415

    target = canonical_link_id(link_id)
    if not target:
        return None
    for summary in observations.list_observation_sets():
        set_id = summary.get("id")
        if not set_id:
            continue
        record = observations.get_observation_set(str(set_id))
        if not record:
            continue
        for sites in (record.get("aggregates") or {}).values():
            for site_id, entry in (sites or {}).items():
                movements = entry.get("movements") or {}
                for mid, volume in movements.items():
                    if movement_from_link(mid) != target:
                        continue
                    return {
                        "node_id": str(site_id),
                        "node_name": entry.get("site_name") or str(site_id),
                        "total_real": entry.get("total"),
                        "total_sim_basam": None,
                        "count_basis": "turning_counts",
                        "directions": [
                            {"direction": m, "real": v.get("volume"), "sim_basam": 0}
                            for m, v in movements.items()
                        ],
                    }
    return None


#: Every V7 finding code ``services.agent.genesis_fixes`` maps to
#: ``set_link_geometry`` -- the codes :func:`derive_v7_corrected_polyline`
#: answers for. Mirrors the registration block in that module; the mirror
#: itself is pinned by ``tests/test_genesis_fix_v7_polyline.py``.
V7_GEOMETRY_FINDING_CODES = (
    "v7_link_without_geometry",
    "v7_zero_length_link",
    "v7_link_below_generator_floor",
    "v7_links_share_one_polyline",
)

#: The one draft field a V7 correction may ever come from -- recorded verbatim
#: in every derivation's provenance so the evidence names its own source.
_V7_DRAFT_SOURCE_FIELD = "links[].points"


def _rounded_polyline(points: Any, decimals: int = OVERLAP_DECIMALS) -> tuple | None:
    """The polyline rounded for EQUALITY at the judge's own precision
    (``gate.OVERLAP_DECIMALS`` -- the exact rounding ``_check_v7`` groups
    shared polylines with), or ``None`` for anything malformed."""
    try:
        return tuple((round(float(p[0]), decimals), round(float(p[1]), decimals)) for p in points)
    except (TypeError, ValueError, IndexError):
        return None


@lru_cache(maxsize=8)
def _emitted_polylines_cached(
    path_text: str, mtime_ns: int, size: int
) -> dict[str, tuple] | None:
    """``services.genesis.diff.load_topology``'s per-link geometry for one
    ``.inpx``, cached on ``(path, mtime, size)`` so the fixes-listing route --
    which re-derives per FINDING -- parses a real refused artifact once, not
    once per shared-polyline group (a real Daejeon region job carries 66 of
    them). The mtime/size key means a re-gated or fixed artifact re-parses.
    Function-local import for the same reason :func:`derive_v6_node_evidence`
    imports locally: keep this module's import surface minimal."""
    from services.genesis.diff import load_topology  # noqa: PLC0415

    topology, _findings = load_topology(Path(path_text))
    if topology is None:
        return None
    return dict(topology.geometry)


def _emitted_link_polylines(job_id: str) -> dict[str, tuple] | None:
    """The polylines the job's CURRENT ``.inpx`` artifact actually carries,
    by link id -- or ``None`` when there is no readable artifact. Never
    raises: like :func:`job_draft_or_none`, an unreadable artifact means a
    caller derives LESS, not that it fails outright."""
    try:
        path = artifact_path(job_id, "inpx")
        stat = path.stat()
    except (GenesisJobNotFoundError, OSError):
        return None
    return _emitted_polylines_cached(str(path), stat.st_mtime_ns, stat.st_size)


def derive_v7_corrected_polyline(
    code: str,
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
) -> dict[str, Any]:
    """Unit #34: the ``corrected_points_xy`` a V7 geometry fix may honestly
    carry -- the DRAFT's OWN declared polyline for the finding's link id
    (``*.genesis.json`` ``links[].points``, the pre-emission record), and
    NOTHING else. Never the other twin's polyline, never interpolated, never
    invented -- when the draft's own record cannot correct the artifact, this
    returns no points and the registry's ``NO_TOOL_WITHOUT_FABRICATION``
    refusal stands, now carrying the measured reason.

    Returns ``{"corrected_points_xy": [[x, y], ...] | None, "provenance":
    {...}}``. ``provenance`` always names the source (``source_artifact`` /
    ``source_field`` / ``link_id`` / ``job_id``); when no points were derived
    it adds ``reason_code`` + ``detail`` -- the measured fact, threaded into
    the builder's refusal evidence and rationale by
    ``services.agent.genesis_fixes._build_v7_geometry_fix``.

    The rules, in the order they are measured (each named by its
    ``reason_code``):

    * ``no_draft_record`` / ``no_draft_census`` -- the finding names no
      addressable link, or the draft census is absent/unreadable, or it has no
      link record for that id (connectors have no ``links[]`` record, and
      ``set_link_geometry`` refuses connectors anyway).
    * ``draft_record_unusable`` -- the draft's own record could not clear the
      finding: fewer than 2 points, degenerate (duplicate consecutive points /
      zero length -- ``SetLinkGeometryParams`` would refuse it), zero-length at
      the file's precision for ``v7_zero_length_link``, or below the finding's
      own floor for ``v7_link_below_generator_floor``.
    * ``draft_shares_the_corruption`` (``v7_links_share_one_polyline`` only) --
      the draft's OWN records for the finding's links are already identical at
      the judge's precision: the corruption happened UPSTREAM of the draft, so
      the census has no pre-corruption record to offer. This is what every
      real refused Daejeon job measured as of 2026-08-14 (164/164 findings
      across 4 jobs) -- the honest refusal IS the common case today.
    * ``emitted_geometry_unreadable`` -- the artifact's current polyline for
      this link could not be read, so neither the no-op rule nor the
      point-count rule below could be measured.
    * ``point_count_differs`` -- ``set_link_geometry`` requires exactly as
      many points as the link already has (COM cannot add or remove a polyline
      point; ``tools_edit.py``'s own landmine 3), so a draft record with a
      different count can never be applied by the registered verb.
    * ``draft_equals_emitted`` -- the draft's record IS the polyline the
      artifact already carries, at the judge's precision: re-applying it would
      change nothing the gate can see. A no-op dressed as a fix is a refusal.
    """
    provenance: dict[str, Any] = {
        "source_artifact": "genesis_json",
        "source_field": _V7_DRAFT_SOURCE_FIELD,
        "link_id": None,
        "job_id": job_id,
    }

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "corrected_points_xy": None,
            "provenance": {**provenance, "reason_code": reason_code, "detail": detail},
        }

    from services.agent.genesis_fixes import expected_fix_target  # noqa: PLC0415

    link_id_text = expected_fix_target(code, finding)
    if not link_id_text:
        return _nothing("no_draft_record", "the finding names no addressable link id")
    provenance["link_id"] = link_id_text

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    try:
        link = draft.link_by_id(int(link_id_text))
    except (TypeError, ValueError):
        link = None
    if link is None:
        return _nothing(
            "no_draft_record",
            f"the draft census declares no links[] record for id {link_id_text}",
        )

    declared = [[float(x), float(y)] for x, y in link.points]
    if len(declared) < 2:
        return _nothing(
            "draft_record_unusable",
            f"the draft's own record has {len(declared)} point(s) -- no shape to restore",
        )
    if any(declared[i] == declared[i - 1] for i in range(1, len(declared))) or link.length_m <= 0:
        return _nothing(
            "draft_record_unusable",
            "the draft's own record is degenerate (duplicate consecutive points or zero length)",
        )
    if code == "v7_zero_length_link" and link.length_m < ZERO_LENGTH_M:
        return _nothing(
            "draft_record_unusable",
            f"the draft's own record is {link.length_m:.6f} m -- still zero at the file's "
            f"precision ({ZERO_LENGTH_M} m)",
        )
    if code == "v7_link_below_generator_floor":
        context = dict(finding.get("context") or {})
        try:
            floor_m = float(context.get("floor_m"))
        except (TypeError, ValueError):
            floor_m = SHORT_LINK_FLOOR_M
        if link.length_m < floor_m:
            return _nothing(
                "draft_record_unusable",
                f"the draft's own record is {link.length_m:.3f} m -- still below the "
                f"{floor_m} m floor the finding names",
            )

    declared_key = _rounded_polyline(declared)
    if code == "v7_links_share_one_polyline":
        context = dict(finding.get("context") or {})
        for other_id in context.get("links") or []:
            other_text = str(other_id)
            if other_text == link_id_text:
                continue
            try:
                other = draft.link_by_id(int(other_text))
            except (TypeError, ValueError):
                other = None
            if other is not None and _rounded_polyline(other.points) == declared_key:
                return _nothing(
                    "draft_shares_the_corruption",
                    f"the draft's own records for links {link_id_text} and {other_text} "
                    "already share one polyline -- the corruption happened upstream of "
                    "the draft",
                )

    emitted = _emitted_link_polylines(job_id) if job_id else None
    current = emitted.get(link_id_text) if emitted else None
    if not current:
        return _nothing(
            "emitted_geometry_unreadable",
            f"the emitted artifact's current polyline for link {link_id_text} could not "
            "be read, so the correction could not be verified against it",
        )
    if len(current) != len(declared):
        return _nothing(
            "point_count_differs",
            f"the artifact carries {len(current)} point(s) for link {link_id_text} but the "
            f"draft declares {len(declared)} -- set_link_geometry cannot add or remove a "
            "polyline point (COM has no Add/Remove on the point container)",
        )
    if _rounded_polyline(current) == declared_key:
        return _nothing(
            "draft_equals_emitted",
            f"the draft's record for link {link_id_text} is the polyline the artifact "
            "already carries -- re-applying it would change nothing",
        )

    return {
        "corrected_points_xy": declared,
        "provenance": {**provenance, "draft_point_count": len(declared)},
    }


#: Every V3 finding code ``services.agent.genesis_fixes`` registers under
#: ``CONNECTIVITY_IS_NOT_INVENTED`` -- the codes
#: :func:`derive_v3_source_testimony` answers for. Mirrors the registration
#: block in that module the same way :data:`V7_GEOMETRY_FINDING_CODES` mirrors
#: its own; the mirror itself is pinned by
#: ``tests/test_genesis_fix_v3_source.py``.
V3_CONNECTIVITY_FINDING_CODES = (
    "v3_orphan_link",
    "v3_isolated_zone",
    "v3_unreachable_zone",
    "v3_trapped_zone",
    "v3_broken_zone",
    "v3_input_cannot_reach_an_exit",
    "v3_silent_departure_point",
)

#: The one source declaration a V3 testimony may ever come from -- way/node
#: membership (``<way><nd ref=.../>``) in the job's own stored source
#: ``.osm``. Recorded verbatim in every derivation's provenance so the
#: evidence names its own source, mirroring :data:`_V7_DRAFT_SOURCE_FIELD`.
_V3_SOURCE_FIELD = "way/nd"


@lru_cache(maxsize=8)
def _source_osm_index_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any] | None:
    """The connectivity-relevant index of one source ``.osm`` -- which
    drivable ways touch which nodes, each way's node order and oneway
    declaration, and node coordinates -- cached on ``(path, mtime, size)``
    with the same bounded discipline :func:`_emitted_polylines_cached` uses,
    and for the same reason: the fixes-listing route re-derives per FINDING,
    and a real refused Daejeon job's source file (0.9 MB, 534 drivable ways)
    must be parsed once per version of the file, not once per finding.

    The parse is :func:`services.genesis.osm.parse_osm_file` with DEFAULTS --
    no ``exclude_service``, no requested-region trim -- because the question
    this index answers is what the SOURCE ITSELF declares, not what the
    generation kept of it. ``None`` when the file refuses to parse or holds
    no drivable ways. Function-local import for the same import-surface
    reason its V7 sibling states.
    """
    from services.genesis.osm import parse_osm_file  # noqa: PLC0415

    extract = parse_osm_file(Path(path_text))
    if extract.refusals or not extract.ways:
        return None
    # A file can declare the same way id twice (seen in a real Overpass
    # export: way 1232703541 in job b3ef6abb9a85's source); last-wins here,
    # and connected ids are deduped at measurement time.
    ways: dict[int, tuple[tuple[int, ...], int]] = {}
    way_names: dict[int, str] = {}
    node_ways: dict[int, set[int]] = {}
    for way in extract.ways:
        ways[way.id] = (tuple(way.node_ids), way.oneway)
        # The name rides along for unit #39's same-road continuation walk
        # (last-wins with the same duplicate-id discipline as ``ways``).
        way_names[way.id] = way.name
        for node_id in set(way.node_ids):
            node_ways.setdefault(node_id, set()).add(way.id)
    return {
        "ways": ways,
        "way_names": way_names,
        "node_ways": {node: tuple(sorted(ids)) for node, ids in node_ways.items()},
        "node_latlon": {node.id: (node.lat, node.lon) for node in extract.nodes.values()},
        "non_drivable_node_highways": {
            node: tuple(sorted(kinds))
            for node, kinds in (extract.non_drivable_node_highways or {}).items()
        },
    }


def _source_osm_index(job_id: str) -> dict[str, Any] | None:
    """The cached source index for one job's own stored ``.osm`` -- or
    ``None`` when the job has no source artifact (a fix job's ``source_osm``
    is ``None`` by construction) or the file is gone/unreadable. Never
    raises, for the same derive-LESS reason :func:`_emitted_link_polylines`
    states."""
    try:
        path = artifact_path(job_id, "source_osm")
        stat = path.stat()
    except (GenesisJobNotFoundError, OSError):
        return None
    return _source_osm_index_cached(str(path), stat.st_mtime_ns, stat.st_size)


def _way_departs_node(
    node_ids: tuple[int, ...], oneway: int, node_id: int, *, travel_forward: bool | None
) -> bool:
    """Whether ONE source way's own declaration lets a vehicle travel AWAY
    from ``node_id`` along it.

    ``travel_forward`` is ``None`` for a way OTHER than the finding's own --
    either onward direction the oneway declaration permits counts as a
    departure. For the finding's OWN way it is the link's travel direction
    (``DraftLink.forward``), and only CONTINUING through the node in that
    direction counts: turning back up the carriageway just arrived on is a
    U-turn, not a connection the network lost.
    """
    last = len(node_ids) - 1
    if last < 1:
        return False
    positions = [i for i, ref in enumerate(node_ids) if ref == node_id]
    if not positions:
        return False
    forward_possible = oneway >= 0 and any(i < last for i in positions)
    backward_possible = oneway <= 0 and any(i > 0 for i in positions)
    if travel_forward is None:
        return forward_possible or backward_possible
    return forward_possible if travel_forward else backward_possible


def derive_v3_source_testimony(
    code: str,
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
) -> dict[str, Any]:
    """Unit #36: does the JOB'S OWN SOURCE ``.osm`` declare the connection a
    V3 connectivity finding says the network lost?

    A connectivity gap has two very different causes and the registry's
    ``CONNECTIVITY_IS_NOT_INVENTED`` refusal (correctly) covers both: the
    WORLD genuinely ends there (the source itself declares no onward way), or
    the PIPELINE lost a connection the source declares (two ways sharing a
    node, dropped somewhere between parse and emission). This deriver reads
    the job's own stored source ``.osm`` at the finding's location and
    answers which case this finding is -- evidence for the refusal's
    rationale, NEVER a licence to invent the connector: the disposition tier
    stays ``NOT_AUTOMATABLE`` regardless of the answer.

    Returns ``{"source_testimony": {...} | None, "provenance": {...}}``,
    mirroring :func:`derive_v7_corrected_polyline`'s own shape. When the
    source testifies, ``source_testimony`` is ``{source_artifact, way_id,
    node_id, connected_way_ids, coordinates}`` -- ``connected_way_ids`` the
    deduped, sorted ids of every drivable way the source declares a vehicle
    could depart the node on (including the finding's own way when the source
    declares it CONTINUES past the node in the direction of travel), and
    ``coordinates`` the node's own lat/lon from the source. ``provenance``
    always names the source (``source_artifact`` / ``source_field`` /
    ``link_id`` / ``way_id`` / ``node_id`` / ``node_source`` / ``job_id``);
    when there is no testimony it adds ``reason_code`` + ``detail`` -- the
    measured fact, threaded into the registry's refusal rationale by
    ``services.agent.genesis_fixes._build_v3_connectivity_no_fix``.

    How a finding maps back to the source: the finding's own ``context
    ['link_id']`` (V3's census-sourced silent-departure findings carry it) or
    the leading id of its ``"<id> (<name>)"`` subject names the draft link;
    the draft's census record for that link carries ``osm_way_id``; the
    questioned node is the connectivity census's own ``node_osm_id`` for that
    link (``metadata['connectivity']['dangling_approaches']`` -- the SAME
    census ``gate._check_v3`` reads) when an entry exists, else the link's
    own ``to_node_osm_id`` (its departure end). ``provenance['node_source']``
    records which. The arrival end of a ``v3_orphan_link`` is a known
    residual: this deriver asks about the departure end only.

    The named "no testimony" reasons, in the order they are measured:

    * ``no_draft_record`` -- the finding names no addressable link id, or the
      draft census has no ``links[]`` record for it.
    * ``no_draft_census`` -- the census is absent, unreadable, or invalid.
    * ``source_artifact_missing`` -- the job's source ``.osm`` is absent,
      unreadable, unparseable, or holds no drivable ways.
    * ``way_not_in_source`` -- the census names a way the source does not
      declare (drivably).
    * ``node_not_in_source`` -- the questioned node is on no drivable way in
      the source.
    * ``source_shares_the_gap`` -- the measured world-gap: no drivable way in
      the source can be traveled away from the node (the detail counts the
      ways that touch it and names any non-drivable continuation, e.g. a
      corridor that carries on only as a footway).
    """
    provenance: dict[str, Any] = {
        "source_artifact": "source_osm",
        "source_field": _V3_SOURCE_FIELD,
        "link_id": None,
        "way_id": None,
        "node_id": None,
        "job_id": job_id,
    }

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "source_testimony": None,
            "provenance": {**provenance, "reason_code": reason_code, "detail": detail},
        }

    context = dict(finding.get("context") or {})
    link_id_raw = context.get("link_id")
    if link_id_raw is None:
        subject_head = str(finding.get("subject") or "").strip().split(" (", 1)[0].strip()
        link_id_raw = subject_head or None
    try:
        link_id = int(str(link_id_raw))
    except (TypeError, ValueError):
        return _nothing("no_draft_record", "the finding names no addressable link id")
    provenance["link_id"] = str(link_id)

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    link = draft.link_by_id(link_id)
    if link is None:
        return _nothing(
            "no_draft_record",
            f"the draft census declares no links[] record for id {link_id}",
        )
    way_id = int(link.osm_way_id)
    provenance["way_id"] = way_id

    node_id: int | None = None
    node_source = "draft_link_departure_end"
    connectivity = (draft.metadata or {}).get("connectivity")
    if isinstance(connectivity, Mapping):
        for entry in connectivity.get("dangling_approaches") or []:
            if not isinstance(entry, Mapping):
                continue
            try:
                entry_link = int(str(entry.get("link_id")))
            except (TypeError, ValueError):
                continue
            if entry_link == link_id and entry.get("node_osm_id") is not None:
                try:
                    node_id = int(str(entry.get("node_osm_id")))
                    node_source = "connectivity_census"
                except (TypeError, ValueError):
                    node_id = None
                break
    if node_id is None:
        node_id = int(link.to_node_osm_id)
        node_source = "draft_link_departure_end"
    provenance["node_id"] = node_id
    provenance["node_source"] = node_source

    index = _source_osm_index(job_id) if job_id else None
    if index is None:
        return _nothing(
            "source_artifact_missing",
            "this job's source .osm is absent, unreadable, unparseable, or holds no "
            "drivable ways -- the source's own declaration could not be consulted",
        )
    if way_id not in index["ways"]:
        return _nothing(
            "way_not_in_source",
            f"the census names way {way_id} for link {link_id}, but the source holds no "
            "drivable way with that id",
        )
    touching = index["node_ways"].get(node_id, ())
    if not touching:
        return _nothing(
            "node_not_in_source",
            f"node {node_id} is on no drivable way in the source",
        )

    departing: set[int] = set()
    for candidate_id in touching:
        node_refs, oneway = index["ways"][candidate_id]
        travel = bool(link.forward) if candidate_id == way_id else None
        if _way_departs_node(node_refs, oneway, node_id, travel_forward=travel):
            departing.add(candidate_id)

    if not departing:
        others = len([w for w in touching if w != way_id])
        if others:
            detail = (
                f"{others} other drivable way(s) touch node {node_id} in the source, but "
                "none can be traveled away from it (every oneway declaration points into "
                "the node)"
            )
        else:
            detail = (
                f"the source's way {way_id} ends at node {node_id} and no other drivable "
                "way touches that node"
            )
        non_drivable = index["non_drivable_node_highways"].get(node_id, ())
        if non_drivable:
            detail += f"; the corridor continues only as {', '.join(non_drivable)}"
        return _nothing("source_shares_the_gap", detail)

    connected = sorted(departing)
    lat_lon = index["node_latlon"].get(node_id)
    testimony: dict[str, Any] = {
        "source_artifact": "source_osm",
        "way_id": way_id,
        "node_id": node_id,
        "connected_way_ids": connected,
        "coordinates": (
            {"lat": lat_lon[0], "lon": lat_lon[1]} if lat_lon is not None else None
        ),
    }
    return {
        "source_testimony": testimony,
        "provenance": {**provenance, "connected_way_ids": connected},
    }


# ---------------------------------------------------------------------------
# Unit #35 — the V3 TREATMENT. A connection whose FACT has a named source
# (the job's own .osm, or a human who takes responsibility for it) becomes a
# connector this system builds MECHANICALLY out of two link ends that already
# exist. CONNECTIVITY_IS_NOT_INVENTED survives intact: what is never invented
# is the CONNECTION, and neither branch below invents one.
# ---------------------------------------------------------------------------

#: The ONE V3 finding code this treatment answers for. Every other V3 code
#: stays the pure refusal unit #36 left it as, and deliberately so: a
#: silent departure point is the only V3 finding that names ONE link END —
#: exactly the shape ``add_connector``'s from-side needs. ``v3_orphan_link``
#: is dead at BOTH ends (one departing connector answers half of it),
#: ``v3_*_zone`` names a whole component, and ``v3_input_cannot_reach_an_exit``
#: names a reachability property of a path, not an end. Deriving a from-link
#: END out of any of those would be this module inventing the address, which
#: is the same fabrication in a different coat.
V3_TREATABLE_FINDING_CODE = "v3_silent_departure_point"

#: How a connector plan's CONNECTION fact was established. A plan carries
#: exactly ONE of these and a plan with neither is not a plan — the whole
#: point of the unit is that the fact always has a named source.
CONNECTOR_BASIS_SOURCE_TESTIMONY = "source_testimony"
CONNECTOR_BASIS_HUMAN_ATTESTATION = "human_attestation"

#: The furthest two link ends may be apart for a connector between them to be
#: a junction MOVEMENT rather than fabricated road. INHERITED from the
#: generator's own declared node scale (``draft.NODE_HALF_SIZE_M`` — the
#: half-box every node this generator builds occupies), doubled because a
#: movement crosses a WHOLE node, not half of one. Deliberately not a fresh
#: number chosen here: a bound this module picked for itself would be exactly
#: the kind of invented quantity ``CONNECTIVITY_IS_NOT_INVENTED`` refuses.
MAX_CONNECTOR_SPAN_M = 2 * NODE_HALF_SIZE_M

#: How many nearest candidate to-links an attestation picker is offered. A cap,
#: not a filter: :data:`MAX_CONNECTOR_SPAN_M` already decides which links are
#: candidates at all, and this only bounds what a UI has to render.
MAX_ATTESTATION_CANDIDATES = 8

#: The one draft field a connector plan's TO-side may ever come from, recorded
#: verbatim in every derivation's provenance, mirroring
#: :data:`_V7_DRAFT_SOURCE_FIELD` / :data:`_V3_SOURCE_FIELD`.
_V3_CONNECTOR_SOURCE_FIELD = "links[].points"


def _link_end_gap_m(from_link: Any, to_link: Any) -> float | None:
    """Metres between the FROM link's departure end and the TO link's start —
    the span a connector between them would have to cover. ``None`` when
    either polyline is too short to have an end at all."""
    try:
        x0, y0 = from_link.points[-1]
        x1, y1 = to_link.points[0]
    except (IndexError, TypeError, ValueError):
        return None
    return math.hypot(float(x1) - float(x0), float(y1) - float(y0))


def _v3_finding_link(finding: Mapping[str, Any], draft: "DraftNetwork | None") -> Any:
    """The draft link a V3 finding addresses — read the SAME way
    :func:`derive_v3_source_testimony` reads it (``context['link_id']`` first,
    the leading id of the ``"<id> (<name>)"`` subject second), so a treatment
    and its testimony can never end up talking about different links."""
    context = dict(finding.get("context") or {})
    link_id_raw = context.get("link_id")
    if link_id_raw is None:
        subject_head = str(finding.get("subject") or "").strip().split(" (", 1)[0].strip()
        link_id_raw = subject_head or None
    try:
        link_id = int(str(link_id_raw))
    except (TypeError, ValueError):
        return None
    if draft is None:
        return None
    return draft.link_by_id(link_id)


def _connector_params_between(from_link: Any, to_link: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """``(add_connector params | None, measurements)`` for a connector from
    ``from_link``'s departure end to ``to_link``'s start — the WHOLE of this
    unit's mechanical construction, in one function so the plan and the
    attestation picker can never construct it two different ways.

    Nothing here decides whether the two ends SHOULD be joined; that is the
    named source's job (the caller's ``testimony``/``attestation``). This only
    answers whether they CAN be, out of what the census already declares:
    both ends must exist, the span between them must be within the
    generator's own node scale (:data:`MAX_CONNECTOR_SPAN_M`), and the
    generator's own ``connector_lane_pairs`` over its own ``classify_turn``
    supplies the lane mapping. ``measurements`` always carries what was
    measured (``span_m``/``max_span_m``, and ``turn``/``heading_change_deg``/
    ``lane_pairs`` once they are known) plus, when no params could be built, a
    ``reason_code`` + ``detail``.
    """
    measurements: dict[str, Any] = {}
    if to_link is None or len(getattr(to_link, "points", ())) < 2 or len(from_link.points) < 2:
        return None, {
            **measurements,
            "reason_code": "degenerate_link_geometry",
            "detail": "one of the two links has fewer than 2 census points, so it has no end to "
            "build a connector from or to",
        }
    if to_link.id == from_link.id:
        return None, {
            **measurements,
            "reason_code": "attested_link_is_the_finding_link",
            "detail": f"link {from_link.id} cannot be joined to itself",
        }
    span = _link_end_gap_m(from_link, to_link)
    measurements["span_m"] = round(float(span), 3) if span is not None else None
    measurements["max_span_m"] = MAX_CONNECTOR_SPAN_M
    if span is None or span > MAX_CONNECTOR_SPAN_M:
        return None, {
            **measurements,
            "reason_code": "span_exceeds_node_scale",
            "detail": f"link {from_link.id}'s departure end and link {to_link.id}'s start are "
            f"{measurements['span_m']} m apart, past this generator's own node scale "
            f"({MAX_CONNECTOR_SPAN_M} m) — a connector across that gap would be road, not a "
            "turn through a junction",
        }
    in_dx = float(from_link.points[-1][0]) - float(from_link.points[-2][0])
    in_dy = float(from_link.points[-1][1]) - float(from_link.points[-2][1])
    out_dx = float(to_link.points[1][0]) - float(to_link.points[0][0])
    out_dy = float(to_link.points[1][1]) - float(to_link.points[0][1])
    turn, heading_change = classify_turn(in_dx, in_dy, out_dx, out_dy)
    pairs = connector_lane_pairs(turn, from_link.lanes, to_link.lanes)
    measurements["turn"] = turn
    measurements["heading_change_deg"] = round(float(heading_change), 3)
    measurements["lane_pairs"] = [[int(a), int(b)] for a, b in pairs]
    if not pairs:  # pragma: no cover - connector_lane_pairs always returns >=1 pair
        return None, {
            **measurements,
            "reason_code": "lane_mapping_not_expressible",
            "detail": "this generator's own lane-pairing rule produced no lane pair for this "
            "movement",
        }
    params = {
        "from_link": str(from_link.id),
        "from_lanes": [int(a) for a, _b in pairs],
        "from_pos_m": round(float(from_link.length_m), 3),
        "to_link": str(to_link.id),
        "to_lanes": [int(b) for _a, b in pairs],
        "to_pos_m": 0.0,
        # Deterministic, ASCII, and derived only from the two ids: a
        # re-derivation at apply time has to produce this byte-for-byte.
        "name": f"V3 departure {from_link.id}->{to_link.id}",
    }
    return params, measurements


def derive_v3_attestation_candidates(
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    limit: int = MAX_ATTESTATION_CANDIDATES,
) -> list[dict[str, Any]]:
    """Every draft link a connector from this finding's departure end could
    physically be BUILT to, nearest first — the picker a human attesting "the
    real road continues here" names their target from.

    MEASURED, never a proposal: each entry carries the metres between the two
    link ends, and nothing here says the road does continue. Only ends a
    connector could actually be CONSTRUCTED to are listed
    (:func:`_connector_params_between` — the same construction the plan
    itself uses, so a candidate can never be one the plan would then refuse),
    and each entry carries that construction as ``params``: an attesting human
    names a target, and the payload their approval submits is this derivation,
    not something a client made up. The apply path re-derives it and refuses a
    payload that differs, so the preview is a convenience, never the authority.

    Empty is a real answer: a departure end with no reachable link end in the
    model has nothing to attest TO, and the honest response is a regeneration
    over a larger extract, not an attestation.
    """
    from_link = _v3_finding_link(finding, draft)
    if from_link is None or draft is None:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for link in draft.links:
        if link.id == from_link.id:
            continue
        params, measurements = _connector_params_between(from_link, link)
        if params is None:
            continue
        gap = float(measurements["span_m"])
        scored.append(
            (
                gap,
                {
                    "link_id": str(link.id),
                    "name": link.name,
                    "way_id": link.osm_way_id,
                    "lanes": link.lanes,
                    "starts_at_node_osm_id": link.from_node_osm_id,
                    "distance_m": measurements["span_m"],
                    "turn": measurements["turn"],
                    "params": params,
                },
            )
        )
    scored.sort(key=lambda item: (item[0], item[1]["link_id"]))
    return [entry for _gap, entry in scored[:limit]]


def derive_v3_connector_plan(
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    testimony: Mapping[str, Any] | None = None,
    attestation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Unit #35: the ``add_connector`` params a V3 silent-departure fix may
    honestly carry — or nothing, with a named measured reason.

    Returns ``{"connector_params": {...} | None, "provenance": {...}}``,
    mirroring :func:`derive_v7_corrected_polyline` / :func:`derive_v3_source_testimony`.

    **Where each field comes from, and which of them is nobody's invention.**
    The CONNECTION — that a vehicle may leave this end and join that link — is
    never derived here: it arrives either as the source's own testimony
    (:func:`derive_v3_source_testimony`, the job's own ``.osm`` declaring ways
    that share the node) or as a human attestation naming the target and its
    basis. Everything else is mechanical construction from two link ends the
    census already declares:

    * ``from_link`` — the finding's own link; ``from_pos_m`` its declared
      length (its departure end, which is what the finding is ABOUT);
    * ``to_link`` — testified: the draft link carrying a testified way whose
      census record says it BEGINS at the testified node (way_id -> link, the
      reverse of the mapping unit #36 used; several candidates are ranked by
      the measured distance between the two ends and the nearest wins, with
      every candidate recorded). Attested: the link the human named, verified
      to exist in the census and not to be the finding's own link;
    * ``to_pos_m`` — ``0.0``, the to-link's start. A connector joining a link
      anywhere else would leave the road behind it unreachable;
    * ``from_lanes``/``to_lanes`` — ``draft.connector_lane_pairs`` over
      ``draft.classify_turn``: the generator's OWN lane-pairing rule, the same
      one every other connector in this network was built with, reused rather
      than re-derived.

    The span between the two ends is measured and bounded by
    :data:`MAX_CONNECTOR_SPAN_M`; beyond it, the "connector" would be road
    rather than a turn, and this refuses by name instead.

    **Which source is asked, in which order.** The job's own ``.osm`` first;
    a human's attestation when the source has nothing to offer OR when it
    testifies to a connection this model has no link for (measured on job
    ``b3ef6abb9a85``: the source's own way carries on past the node and the
    pipeline emitted no link for the continuation). The second half matters —
    that state is one the refusal INVITES an attestation for, and ignoring
    the attestation it then receives would be a silent drop of the reader's
    own claim.

    Named reasons for no plan (``provenance['reason_code']``):
    ``no_draft_census`` / ``no_draft_record`` (the finding maps to no census
    link), ``no_named_source`` (neither testimony nor attestation — the bare
    refusal unit #36 left, unchanged), ``no_draft_link_departs_the_node`` (the
    source testifies, no census link carrying a testified way begins at that
    node, and nobody attested one — a connector cannot be built to a link that
    is not there),
    ``attestation_without_target`` / ``attestation_without_basis`` /
    ``attestation_without_attribution`` (an incomplete attestation is refused,
    never completed with a default), ``attested_link_not_in_census``,
    ``attested_link_is_the_finding_link``, ``degenerate_link_geometry``,
    ``span_exceeds_node_scale``, ``lane_mapping_not_expressible``.
    """
    provenance: dict[str, Any] = {
        "source_field": _V3_CONNECTOR_SOURCE_FIELD,
        "basis": None,
        "link_id": None,
        "node_id": None,
        "to_link": None,
    }

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "connector_params": None,
            "provenance": {**provenance, "reason_code": reason_code, "detail": detail},
        }

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    from_link = _v3_finding_link(finding, draft)
    if from_link is None:
        return _nothing(
            "no_draft_record",
            "the finding names no addressable link id, or the census declares no links[] "
            "record for it",
        )
    provenance["link_id"] = str(from_link.id)
    if len(from_link.points) < 2:
        return _nothing(
            "degenerate_link_geometry",
            f"the census record for link {from_link.id} has fewer than 2 points, so it has no "
            "departure end to build a connector from",
        )

    # The source is asked FIRST and a human second — but a human is asked at
    # all, because the source can testify to a connection this model has no
    # link for (measured on job b3ef6abb9a85: the source's own way carries on
    # past the node, and the pipeline emitted no link for the continuation).
    # Ignoring an attestation in that state would have been a silent drop of
    # exactly the claim the reader was invited to make.
    to_link = None
    testimony_failure: dict[str, Any] | None = None
    considered: list[dict[str, Any]] = []
    if isinstance(testimony, Mapping) and testimony.get("connected_way_ids"):
        provenance["basis"] = CONNECTOR_BASIS_SOURCE_TESTIMONY
        node_id = testimony.get("node_id")
        provenance["node_id"] = node_id
        provenance["way_id"] = testimony.get("way_id")
        provenance["connected_way_ids"] = list(testimony["connected_way_ids"])
        testified_ways = {int(w) for w in testimony["connected_way_ids"]}
        for link in draft.links:
            if link.id == from_link.id or int(link.osm_way_id) not in testified_ways:
                continue
            # "its end sits at the testified node": the census's OWN statement
            # that this link BEGINS there, which is the same fact expressed
            # without trusting float geometry. Ranking below is the tie-break,
            # never the test.
            if node_id is None or int(link.from_node_osm_id) != int(node_id):
                continue
            gap = _link_end_gap_m(from_link, link)
            if gap is None:
                continue
            considered.append(
                {
                    "link_id": str(link.id),
                    "way_id": link.osm_way_id,
                    "distance_m": round(gap, 3),
                }
            )
        considered.sort(key=lambda entry: (entry["distance_m"], entry["link_id"]))
        provenance["candidates_considered"] = considered
        if considered:
            to_link = draft.link_by_id(int(considered[0]["link_id"]))
        else:
            testimony_failure = _nothing(
                "no_draft_link_departs_the_node",
                f"the source declares way(s) "
                f"{', '.join(str(w) for w in testimony['connected_way_ids'])} depart node "
                f"{node_id}, but this network's census has no link carrying one of them that "
                f"BEGINS at that node — there is no link end to connect to",
            )

    if to_link is None and attestation is not None:
        provenance["basis"] = CONNECTOR_BASIS_HUMAN_ATTESTATION
        to_link_text = str(attestation.get("to_link") or "").strip()
        basis_text = str(attestation.get("basis") or "").strip()
        attested_by = str(attestation.get("attested_by") or "").strip()
        attested_at = str(attestation.get("attested_at") or "").strip()
        provenance["to_link"] = to_link_text or None
        provenance["attested_by"] = attested_by or None
        provenance["attested_at"] = attested_at or None
        if not to_link_text:
            return _nothing(
                "attestation_without_target",
                "the attestation names no to_link — a human attesting that the road continues "
                "must name WHERE it continues to",
            )
        if not basis_text:
            return _nothing(
                "attestation_without_basis",
                "the attestation carries no basis sentence — an attestation without a stated "
                "basis is a click, not a claim somebody is accountable for",
            )
        if not attested_by or not attested_at:
            return _nothing(
                "attestation_without_attribution",
                "the attestation carries no attester and/or no timestamp — an unattributed "
                "attestation cannot be recorded as one",
            )
        try:
            to_link = draft.link_by_id(int(to_link_text))
        except (TypeError, ValueError):
            to_link = None
        if to_link is None:
            return _nothing(
                "attested_link_not_in_census",
                f"the attested to_link {to_link_text!r} is not a link this network's census "
                "declares",
            )
        # Everything else about the pair -- including "not the finding's own
        # link" -- is _connector_params_between's single implementation below.
    elif to_link is None:
        # The source could not be followed and nobody claimed the connection:
        # the testimony's own measured reason when there was one, else the
        # bare "nothing named this" that unit #36's refusal already covers.
        return testimony_failure or _nothing(
            "no_named_source",
            "neither the job's own source .osm nor a human attestation names the connection "
            "this fix would build — the connection fact has no source, so there is no fix",
        )

    provenance["to_link"] = str(to_link.id) if to_link is not None else None
    provenance["to_link_way_id"] = to_link.osm_way_id if to_link is not None else None

    params, measurements = _connector_params_between(from_link, to_link)
    provenance.update({k: v for k, v in measurements.items() if k not in ("reason_code", "detail")})
    if params is None:
        return _nothing(str(measurements["reason_code"]), str(measurements["detail"]))
    return {"connector_params": params, "provenance": provenance}


# ---------------------------------------------------------------------------
# Unit #42 — the THIRD answer a world gap can have. Unit #36 split a V3
# connectivity refusal into "the source declares this connection" (ours) and
# "the source shares the gap" (the world's); unit #35 gave the second one an
# ask-first question — *does the real road continue, and to where?* — that only
# has an answer when the road really does continue. It often does not: a
# department store's parking ramp genuinely ENDS at the public network, and the
# nearest reachable link end is 9.7 m away and belongs to a different road.
# Picking it would be false geometry, and leaving the finding untreated forever
# is the dead end this unit removes: a human who checked the site CONFIRMS the
# road ends, the confirmed way is excluded BY NAME, and the generation is
# re-derived without it (through unit #38's own machine). The judge is not
# touched — V3 stops firing because the way it fired about is no longer in the
# network, which is the only honest way for a finding to go away.
# ---------------------------------------------------------------------------

#: Where a dead-end confirmation's own facts come from — the source's declared
#: way/node membership plus the census's link records — recorded verbatim in
#: every derivation's provenance, mirroring :data:`_V3_SOURCE_FIELD`.
_V3_DEAD_END_SOURCE_FIELD = "way/nd + links[].osm_way_id"

#: The longest basis sentence a confirmation may carry, mirroring the
#: attestation basis cap on ``routers.genesis.GenesisFixAttestationRequest``.
DEAD_END_BASIS_MAX_CHARS = 600


def _declared_excluded_way_ids(job_id: str | None) -> frozenset[int]:
    """The way ids ``job_id``'s OWN record already declares excluded.

    Refuter find (2026-08-18), and the reason this exists: a job's source ``.osm``
    is deliberately never edited, so a sibling that already carries a confirmed
    exclusion holds a source that STILL declares the removed way. Measuring what
    a further exclusion would cut loose against that source measures a network
    nobody is looking at — the first confirmer's road is still in the graph,
    still holding a pocket together that the sibling has already let go of. The
    numbers a second confirmer signs for have to be about the network THEY are
    looking at.

    A record's own ``excluded_ways`` is exactly the right correction and nothing
    more: it is this system's own declaration, re-readable at any time, sitting
    on the job being measured — the top of the evidence hierarchy, not a guess
    about what the pipeline might later do.

    Never raises: an unknown or unreadable job contributes nothing, the same
    derive-LESS discipline :func:`_source_osm_index` keeps.
    """
    if not job_id:
        return frozenset()
    try:
        record = get_job(job_id)
    except GenesisJobError:
        return frozenset()
    found: set[int] = set()
    for entry in record.get("excluded_ways") or []:
        try:
            found.add(int(entry["way_id"]))
        except (KeyError, TypeError, ValueError):  # pragma: no cover - record is ours
            continue
    return frozenset(found)


def _index_without_ways(
    index: Mapping[str, Any], removed: frozenset[int]
) -> Mapping[str, Any]:
    """``index`` with ``removed`` gone from its way graph — a NEW mapping, never
    a mutation: :func:`_source_osm_index` hands out an LRU-cached dict that every
    other derivation on this job shares. The SAME object back when there is
    nothing to remove, so an ordinary generation's measurement is byte-for-byte
    the one it always was."""
    if not removed:
        return index
    return {
        **index,
        "ways": {w: v for w, v in index["ways"].items() if w not in removed},
        "node_ways": {
            node: tuple(w for w in ways if w not in removed)
            for node, ways in index["node_ways"].items()
        },
    }


def _source_way_components(index: Mapping[str, Any], *, without: int | None = None) -> list[set[int]]:
    """The connected components of the SOURCE's own way graph — two ways are
    adjacent when the source declares they share a node — optionally with one
    way id removed.

    Undirected on purpose, and the honest reading of that: this measures what
    the SOURCE DECLARES about adjacency, not what a vehicle can drive. Oneway
    declarations, turn restrictions and the generator's own connector rules can
    all make a source-adjacent pocket unreachable in the emitted network, so a
    component here is a LOWER bound on severance — it can miss a pocket the
    pipeline will later strand, and it never invents one that is not there. The
    pipeline's own isolation pass (``draft.build_draft``, B5-1) remains the
    authority, and it names what it removes; this is the number a human gets to
    see BEFORE they press, which is a different job.

    ``index`` is whatever graph the caller decided the question is about, and
    :func:`derive_v3_dead_end_confirmation` narrows it by the job's OWN declared
    exclusions before asking (:func:`_index_without_ways`). Without that the
    "source declares" reading is a trap on a sibling: the source keeps declaring
    a way an earlier confirmation removed, and the pocket that way was holding
    up reads as safely attached.
    """
    adjacency: dict[int, set[int]] = {way_id: set() for way_id in index["ways"] if way_id != without}
    for way_ids in index["node_ways"].values():
        present = [w for w in way_ids if w != without and w in adjacency]
        for a in present:
            adjacency[a].update(w for w in present if w != a)
    seen: set[int] = set()
    components: list[set[int]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack = [start]
        component: set[int] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(w for w in adjacency[current] if w not in component)
        seen |= component
        components.append(component)
    return components


def _dead_end_collateral(index: Mapping[str, Any], way_id: int) -> dict[str, Any]:
    """What ELSE the source says would come loose if ``way_id`` were excluded.

    The honesty clause of this whole unit: an operator confirms that ONE road
    ends where they saw it end. They do not thereby confirm anything about the
    roads behind it, and an exclusion that quietly severed a neighbourhood
    would be exactly the "completed edit with wrong numbers" this system
    refuses. So the severance is MEASURED and NAMED before the ask.

    Returns ``{"collateral_way_ids", "empties_the_network", "component_ways",
    "surviving_ways"}``. ``collateral_way_ids`` is every way that shares the
    excluded way's own component today and would NOT share the largest
    remaining piece of it — sorted, and empty for a leaf way (the common case,
    and the real Galleria one: a 2-node service stub whose far node no other
    drivable way touches).

    Only the excluded way's OWN component is examined: a way in a different
    component is unaffected by construction, and calling a pocket that was
    already separate "collateral" would inflate the number a human is being
    asked to weigh.
    """
    before = _source_way_components(index)
    home = next((component for component in before if way_id in component), {way_id})
    remaining = sorted(
        (component for component in _source_way_components(index, without=way_id) if component & home),
        key=lambda component: (-len(component), min(component)),
    )
    survivor = remaining[0] if remaining else set()
    collateral = sorted({w for component in remaining[1:] for w in component})
    return {
        "collateral_way_ids": collateral,
        # A network of exactly one drivable way: removing it leaves nothing to
        # emit, and ``draft`` would answer with an opaque "no node coordinates
        # to project". Named here instead, before a job directory exists.
        "empties_the_network": len(index["ways"]) <= 1,
        "component_ways": len(home),
        "surviving_ways": len(survivor),
    }


def derive_v3_dead_end_confirmation(
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
    testimony: Mapping[str, Any] | None = None,
    testimony_provenance: Mapping[str, Any] | None = None,
    confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Unit #42: may a human confirm that the road REALLY ends here — and, when
    one has, what exactly does that exclude?

    Returns ``{"dead_end_confirmation": {...} | None, "provenance": {...}}``,
    the same shape :func:`derive_v3_source_testimony` and
    :func:`derive_v3_connector_plan` return.

    **The evidence hierarchy is the first door, and the load-bearing one.** A
    finding unit #36 classified as SOURCE TESTIMONY — the job's own ``.osm``
    declares ways sharing the node, drivably — is refused by name
    (``source_testifies_the_connection``). The source is a declaration about the
    world that this system can re-read at any time; a human's confirmation is a
    claim it cannot check. Letting the second erase the first would mean a click
    could delete road the map declares, which is the same fabrication as
    inventing a connector, only subtractive. The honest treatment for a
    testified gap is unit #35's connector, and the refusal says so.

    The invitation is offered for EXACTLY ONE measured state: unit #36 answered
    ``source_shares_the_gap``. Everything else refuses by name:

    * ``source_testifies_the_connection`` — above;
    * ``world_gap_not_measured`` — unit #36 reached some OTHER answer (no census
      record, an unreadable source, a way or node the source does not hold), so
      nothing measured that this is a world gap at all. The underlying reason
      code is quoted rather than hidden;
    * ``no_draft_record`` — the finding maps to no census link, so there is no
      way id to exclude;
    * ``way_not_in_source`` — the census names a way the source does not declare
      drivably, so excluding it would be a parameter naming nothing;
    * ``exclusion_would_empty_the_network`` — nothing would be left to generate:
      either the source (minus what this job already excludes) holds one drivable
      way and it is this one, or every link in this job's own network rides it.
      Both halves are needed and a refuter proved it: the source count alone is
      evadable through ``exclude_service``, the requested-region trim, and a
      chain of exclusions, each of which leaves ways in the source that the
      network does not hold. B5-1's own isolation pass refuses to delete a whole
      network (:data:`~services.genesis.draft.ISOLATION_WOULD_EMPTY_THE_NETWORK`);
      an operator's confirmation gets the same answer, before a job exists.

    **Every measurement below is made on the network THIS job holds**, not on
    everything its source declares — the job's own ``excluded_ways`` are taken
    out of the graph first (:func:`_declared_excluded_way_ids`), and the ones
    taken out are named in the provenance as ``measured_without_way_ids`` so the
    lineage records which graph the number came from. The source file is never
    edited, which is exactly why this correction is needed: a sibling's source
    still declares the road its parent's confirmer removed.

    **What an offered confirmation carries** — all measured, none invented:
    ``way_id`` (the ONE way the exclusion names — never the roads it touches),
    ``node_id`` (the departure end the finding is about), the census's own
    ``links`` carrying that way with their names and lengths, and ``collateral``
    (:func:`_dead_end_collateral` — what the source says would come loose with
    it, whose docstring states exactly what that measurement can and cannot
    see). ``collateral`` is a fact the human is shown, never a silent
    consequence, and it rides into the ledger either way.

    **When a confirmation is SUBMITTED** (``confirmation``), the checks that
    make an attestation a claim rather than a click apply, each refusing rather
    than defaulting: ``confirmation_without_basis``,
    ``confirmation_basis_too_long`` (refused, never truncated — a ledger that
    cuts a person's sentence in half quotes them saying something they did not
    say), ``confirmation_without_attribution`` (attester and server timestamp —
    a request model has no field for either, so attribution cannot be forged),
    ``confirmation_way_mismatch`` (the submitted way id is not the one this
    finding names), and ``collateral_not_acknowledged`` (the acknowledged set
    differs from the re-derived one — the same re-derive-and-compare discipline
    unit #35 applies to connector params, so a client's stale screen cannot
    smuggle an unseen severance past its reader).
    """
    provenance: dict[str, Any] = {
        "source_field": _V3_DEAD_END_SOURCE_FIELD,
        "job_id": job_id,
        "link_id": None,
        "way_id": None,
        "node_id": None,
    }

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "dead_end_confirmation": None,
            "provenance": {**provenance, "reason_code": reason_code, "detail": detail},
        }

    testimony_reason = str((testimony_provenance or {}).get("reason_code") or "")
    if isinstance(testimony, Mapping) and testimony.get("connected_way_ids"):
        ways_text = ", ".join(str(w) for w in testimony["connected_way_ids"])
        return _nothing(
            "source_testifies_the_connection",
            f"the source itself declares way(s) {ways_text} depart node "
            f"{testimony.get('node_id')} here, so this gap is the pipeline's and not the "
            "world's -- a human confirmation cannot delete road the source declares, and the "
            "treatment for a testified gap is the connector unit #35 proposes",
        )
    if testimony_reason != "source_shares_the_gap":
        return _nothing(
            "world_gap_not_measured",
            "nothing measured that the SOURCE shares this gap -- unit #36's derivation "
            f"answered {testimony_reason or '(nothing)'!r} instead, so there is no measured "
            "world gap for a human to confirm",
        )

    from_link = _v3_finding_link(finding, draft)
    if from_link is None or draft is None:
        return _nothing(
            "no_draft_record",
            "the finding names no addressable link id, or the census declares no links[] "
            "record for it -- there is no way id to exclude",
        )
    provenance["link_id"] = str(from_link.id)
    way_id = int(from_link.osm_way_id)
    provenance["way_id"] = way_id
    node_id = (testimony_provenance or {}).get("node_id")
    provenance["node_id"] = node_id

    index = _source_osm_index(job_id) if job_id else None
    if index is None or way_id not in index["ways"]:
        return _nothing(
            "way_not_in_source",
            f"the census names way {way_id} for link {from_link.id}, but the source holds no "
            "drivable way with that id -- an exclusion naming it would exclude nothing",
        )

    # REFUTER FIND (2026-08-18), closed: measure on the graph THIS job holds.
    # The source is evidence and is never edited, so a sibling that already
    # carries a confirmed exclusion keeps a source declaring the removed way --
    # and the severance measurement below, run against that source, would report
    # a pocket as safely attached by a road the first confirmer already took
    # responsibility for removing. Exploited on a two-step chain: with stub A
    # already excluded, confirming stub B measured "nothing else comes loose"
    # while the re-derivation stranded the way hanging off the node they shared.
    # The job's own declared exclusions are the correction, and they are named
    # in the provenance so the lineage says which graph the number was measured
    # on. NEVER the way under consideration: it has to stay in the graph for
    # the question "what would removing it cut loose" to have an answer at all.
    measured_without = _declared_excluded_way_ids(job_id) - {way_id}
    if measured_without:
        provenance["measured_without_way_ids"] = sorted(measured_without)
        index = _index_without_ways(index, measured_without)

    collateral = _dead_end_collateral(index, way_id)
    # The SECOND half of the same find: emptiness is a question about the
    # network, and ``_source_osm_index`` parses with DEFAULTS on purpose (what
    # the SOURCE declares). So ``exclude_service`` and the requested-region trim
    # both leave ways in that count which this generation never modelled, and a
    # source declaring two drivable ways can be a network of one. Exploited by
    # padding a source with a service way: the guard's sentence stayed true and
    # the sibling died on ``draft_refused: osm_no_nodes`` -- the very opaque
    # answer this refusal exists to say instead. The draft is the authority on
    # what the network holds, and it is already in hand.
    network_ways = {int(link.osm_way_id) for link in draft.links}
    if collateral["empties_the_network"] or network_ways <= {way_id}:
        measured = (
            f"the source it re-reads declares {len(index['ways'])} drivable way(s) and this is "
            "one of them"
            if collateral["empties_the_network"]
            else f"every one of this job's {len(draft.links)} link(s) rides it"
        )
        return _nothing(
            "exclusion_would_empty_the_network",
            f"excluding way {way_id} would leave no network to generate -- {measured}. That is "
            "the one thing B5-1's own isolation pass refuses to do as well, and it is refused "
            "here before a job directory exists rather than as an opaque draft failure after",
        )

    carried = [link for link in draft.links if int(link.osm_way_id) == way_id]
    links = [
        {
            "link_id": str(link.id),
            "name": link.name,
            "lanes": int(link.lanes),
            "length_m": round(float(link.length_m), 3),
        }
        for link in sorted(carried, key=lambda link: link.id)
    ]
    offer: dict[str, Any] = {
        "way_id": way_id,
        "node_id": node_id,
        "link_ids": [entry["link_id"] for entry in links],
        "links": links,
        "excluded_length_m": round(sum(float(link.length_m) for link in carried), 3),
        "collateral_way_ids": collateral["collateral_way_ids"],
        "collateral": [
            {"way_id": other, "name": index["way_names"].get(other) or ""}
            for other in collateral["collateral_way_ids"]
        ],
        "source_component_ways": collateral["component_ways"],
        "surviving_component_ways": collateral["surviving_ways"],
    }
    provenance["collateral_way_ids"] = collateral["collateral_way_ids"]
    provenance["link_ids"] = offer["link_ids"]

    if confirmation is None:
        return {"dead_end_confirmation": offer, "provenance": provenance}

    basis_text = str(confirmation.get("basis") or "").strip()
    confirmed_by = str(confirmation.get("confirmed_by") or "").strip()
    confirmed_at = str(confirmation.get("confirmed_at") or "").strip()
    provenance["confirmed_by"] = confirmed_by or None
    provenance["confirmed_at"] = confirmed_at or None
    if not basis_text:
        return _nothing(
            "confirmation_without_basis",
            "the confirmation carries no basis sentence -- confirming that a road ends is a "
            "claim somebody is accountable for, and a claim with no stated basis is a click",
        )
    if len(basis_text) > DEAD_END_BASIS_MAX_CHARS:
        # REFUSED, never truncated. The basis lands in the ledger as the
        # confirmer's own words verbatim, and silently cutting a person's
        # sentence in half would make the record quote them saying something
        # they did not say.
        return _nothing(
            "confirmation_basis_too_long",
            f"the confirmation's basis is {len(basis_text)} characters and the ledger records "
            f"a confirmer's own words verbatim up to {DEAD_END_BASIS_MAX_CHARS} -- shorten it "
            "rather than having this record quote half a sentence",
        )
    if not confirmed_by or not confirmed_at:
        return _nothing(
            "confirmation_without_attribution",
            "the confirmation carries no confirmer and/or no timestamp -- an unattributed "
            "confirmation cannot be recorded as one",
        )
    # ``way_id`` is DERIVED from the finding's own census record, never taken
    # from a caller: the HTTP body has no field for it, so an exclusion cannot
    # be widened past the road the confirming reader actually looked at. A
    # caller MAY assert one (the service layer, a tool, a test), and then it has
    # to be the same one -- defence in depth against a future caller that starts
    # passing the target through, the same redundancy ``apply_genesis_fix``
    # keeps between its route gate and its service gate.
    submitted_way = confirmation.get("way_id")
    if submitted_way is not None:
        try:
            submitted_way_id = int(str(submitted_way))
        except (TypeError, ValueError):
            submitted_way_id = None
        if submitted_way_id != way_id:
            return _nothing(
                "confirmation_way_mismatch",
                f"the confirmation names way {submitted_way!r}, but this finding is about way "
                f"{way_id} (link {from_link.id}) -- a confirmation may only exclude the way its "
                "own finding names, never a road connected to it",
            )
    acknowledged_raw = confirmation.get("acknowledged_collateral_way_ids")
    acknowledged: set[int] = set()
    if acknowledged_raw is not None:
        try:
            acknowledged = {int(str(value)) for value in acknowledged_raw}
        except (TypeError, ValueError):
            acknowledged = {-1}
    if acknowledged != set(collateral["collateral_way_ids"]):
        return _nothing(
            "collateral_not_acknowledged",
            "the confirmation acknowledges "
            f"{sorted(acknowledged) or '(nothing)'} as the ways this exclusion would also cut "
            f"loose, and re-deriving it here names {collateral['collateral_way_ids'] or '(nothing)'} "
            "-- the reader must have seen what they are removing, so a set that does not match "
            "is refused rather than reconciled",
        )
    return {
        "dead_end_confirmation": {
            **offer,
            "confirmed": {
                "way_id": way_id,
                "basis": basis_text,
                "confirmed_by": confirmed_by,
                "confirmed_at": confirmed_at,
                "acknowledged_collateral_way_ids": sorted(acknowledged),
            },
        },
        "provenance": {**provenance, "basis": basis_text},
    }


def normalized_connector_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One comparable shape for an ``add_connector`` payload, so a SUBMITTED
    payload and this module's own DERIVED one are compared by value rather
    than by JSON spelling (``"1"`` vs ``1``, ``100`` vs ``100.0``).

    ``None`` for anything that does not carry the six fields at all — which
    then compares unequal to any real derivation, i.e. a malformed payload is
    refused rather than accidentally matching."""
    if not isinstance(params, Mapping):
        return None
    try:
        return {
            "from_link": str(params["from_link"]),
            "from_lanes": [int(v) for v in params["from_lanes"]],
            "from_pos_m": round(float(params["from_pos_m"]), 3),
            "to_link": str(params["to_link"]),
            "to_lanes": [int(v) for v in params["to_lanes"]],
            "to_pos_m": round(float(params["to_pos_m"]), 3),
            "name": str(params.get("name") or ""),
        }
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Unit #39 — a V4 runway shortage LEARNS whether the boundary did it. The
# registry's v4_lane_drop_without_runway refusal used to say, honestly, "there
# is no evidence this shortfall traces to the requested model boundary" —
# because nothing MEASURED that attribution (the only existing check read the
# DEPARTURE-side dangling census for the finding's own link, and a runway
# shortage's boundary sits at the corridor's upstream ENTRY end, which that
# census never records). This deriver measures it: the judge's own upstream
# runway walk re-traced over the draft, the corridor's upstream terminus
# classified against the census's own boundary markers
# (``metadata['boundary']['way_trims']``'s synthesized nodes — B5-1's
# boundary-synthesized ends — and the dangling census's
# ``model_boundary_correct`` entries), and the SOURCE asked, through unit
# #36's own cached index, how much road it declares beyond that point.
# Evidence for the refusal's rationale, never a tier change: the treatment
# (a boundary-extension proposal) is unit #40's, not this one's.
# ---------------------------------------------------------------------------

#: The ONE V4 finding code this attribution answers for, mirroring
#: :data:`V3_CONNECTIVITY_FINDING_CODES` / :data:`V7_GEOMETRY_FINDING_CODES`.
V4_RUNWAY_FINDING_CODE = "v4_lane_drop_without_runway"

#: Where an attribution's facts may ever come from — the census's own trim
#: records plus way/node membership in the job's stored source ``.osm`` —
#: recorded verbatim in every derivation's provenance, mirroring
#: :data:`_V3_SOURCE_FIELD`.
_V4_ATTRIBUTION_SOURCE_FIELD = "metadata.boundary.way_trims + way/nd"

#: How far the re-trace may disagree with the judge's own ``available_m``
#: before the disagreement is reported instead of an attribution. The judge
#: rounds to 2 decimals and the census stores mm-rounded coordinates, so an
#: honest agreement sits within a few millimetres (measured 0.0003 m on the
#: real job 5e550c739aed); 0.06 m absorbs both roundings and nothing else.
_V4_TRACE_TOLERANCE_M = 0.06

#: Loop bound for the source-side continuation walk — a cap on WAY HOPS, not
#: a measurement: the walk already stops at the deficit, at a name change and
#: at a cycle (visited set), so this only bounds a pathological source.
_V4_SOURCE_WALK_MAX_WAYS = 64


def _latlon_metres(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float:
    """Haversine metres between two ``(lat, lon)`` pairs — 0.0 when either
    is unknown, so a node the source holds no coordinate for contributes no
    invented length (derive LESS, the same rule every deriver here states)."""
    if a is None or b is None:
        return 0.0
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    half_dphi = (phi2 - phi1) / 2.0
    half_dlmb = math.radians(b[1] - a[1]) / 2.0
    h = math.sin(half_dphi) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_dlmb) ** 2
    return 2.0 * 6371008.8 * math.asin(math.sqrt(h))


def _way_arrives_node(
    node_ids: tuple[int, ...], oneway: int, node_id: int, *, travel_forward: bool | None
) -> bool:
    """Whether ONE source way's own declaration lets a vehicle travel INTO
    ``node_id`` along it — the arrival mirror of :func:`_way_departs_node`,
    with the same ``travel_forward`` semantics: ``None`` for a way other than
    the finding's own (either permitted direction counts), the link's travel
    direction for the finding's own way (only a run that genuinely comes from
    UPSTREAM counts; doubling back down the kept carriageway is a U-turn, not
    a continuation)."""
    last = len(node_ids) - 1
    if last < 1:
        return False
    positions = [i for i, ref in enumerate(node_ids) if ref == node_id]
    if not positions:
        return False
    forward_possible = oneway >= 0 and any(i > 0 for i in positions)
    backward_possible = oneway <= 0 and any(i < last for i in positions)
    if travel_forward is None:
        return forward_possible or backward_possible
    return forward_possible if travel_forward else backward_possible


def _arriving_run(
    node_ids: tuple[int, ...],
    oneway: int,
    node_id: int,
    node_latlon: Mapping[int, tuple[float, float]],
    *,
    travel_forward: bool | None = None,
) -> tuple[float, int] | None:
    """The longest run of ONE source way a vehicle may travel INTO
    ``node_id`` on — ``(metres, far_entry_node)`` — or ``None`` when the
    way's own declaration permits no arrival there. ``travel_forward`` as in
    :func:`_way_arrives_node`. The run is measured node-to-node from the
    source's own coordinates; where both directions of a two-way way arrive,
    the longer run is the dominant continuation and wins."""
    last = len(node_ids) - 1
    positions = [i for i, ref in enumerate(node_ids) if ref == node_id]
    if last < 1 or not positions:
        return None

    def _segment(i: int) -> float:
        return _latlon_metres(node_latlon.get(node_ids[i]), node_latlon.get(node_ids[i + 1]))

    best: tuple[float, int] | None = None
    if oneway >= 0 and travel_forward is not False:
        position = max(positions)
        if position > 0:
            best = (sum(_segment(j) for j in range(position)), node_ids[0])
    if oneway <= 0 and travel_forward is not True:
        position = min(positions)
        if position < last:
            length = sum(_segment(j) for j in range(position, last))
            if best is None or length > best[0]:
                best = (length, node_ids[-1])
    return best


def _point_in_requested_region(
    lat: float, lon: float, boundary_meta: Mapping[str, Any]
) -> bool | None:
    """Whether a source coordinate lies inside the REQUESTED region the
    census's own boundary metadata records — the polygon when the region was
    drawn as one, its bbox otherwise, judged by the trim's OWN kernels
    (``osm._inside_polygon`` / ``osm._inside_bbox``, boundary-inclusive) so
    this test and the cut that synthesized the node cannot disagree about
    what "inside" means. ``None`` when the census records no region shape."""
    from services.genesis.osm import _inside_bbox, _inside_polygon  # noqa: PLC0415

    polygon = boundary_meta.get("requested_polygon_wgs84")
    if isinstance(polygon, list) and len(polygon) >= 3:
        try:
            ring = tuple((float(p["lat"]), float(p["lon"])) for p in polygon)
        except (KeyError, TypeError, ValueError):
            ring = None
        if ring:
            return _inside_polygon(lat, lon, ring)
    bbox = boundary_meta.get("requested_bbox_wgs84")
    if isinstance(bbox, Mapping):
        try:
            box = (
                float(bbox["min_lat"]),
                float(bbox["min_lon"]),
                float(bbox["max_lat"]),
                float(bbox["max_lon"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return _inside_bbox(lat, lon, box)
    return None


def _draft_upstream_runway_terminus(
    draft: "DraftNetwork", start_link_id: int, cap_m: float, budget: int
) -> tuple[float, int | None, bool]:
    """``gate._upstream_runway``'s own walk, re-traced over the DRAFT census,
    additionally recording WHERE the best runway runs out.

    Returns ``(best_available_m, terminus_link_id, budget_exhausted)`` —
    the longest simple path of census road feeding ``start_link_id`` (capped
    at ``cap_m``, link lengths only, connectors as edges — exactly the
    judge's own measure), and the upstream-most link of that best path. The
    caller CROSS-CHECKS the distance against the judge's stored
    ``available_m`` before trusting the terminus: this is a re-derivation of
    the judge's number, never a rival measurement.

    Ties between equally long paths resolve deterministically to the smallest
    terminus link id. A corridor that reaches ``cap_m`` is returned capped,
    like the judge's — the caller reports it as a disagreement, since the
    judge only emits this finding when the runway measured SHORT.
    """
    links_by_id = {int(link.id): link for link in draft.links}
    feeders: dict[int, list[int]] = {}
    for connector in draft.connectors:
        feeders.setdefault(int(connector.to_link), []).append(int(connector.from_link))
    for feeding in feeders.values():
        feeding.sort()

    start = links_by_id[start_link_id]
    if start.length_m >= cap_m:
        return start.length_m, start_link_id, False

    best_m = start.length_m
    candidates: list[tuple[float, int]] = []
    stack: list[tuple[int, float, frozenset[int]]] = [
        (start_link_id, start.length_m, frozenset({start_link_id}))
    ]
    expansions = 0
    while stack:
        current, distance, path = stack.pop()
        expansions += 1
        if expansions > budget:
            return best_m, None, True
        extended_any = False
        for previous in feeders.get(current, ()):
            if previous in path:
                continue
            host = links_by_id.get(previous)
            if host is None:
                continue
            extended_any = True
            extended = distance + host.length_m
            if extended >= cap_m:
                return cap_m, previous, False
            if extended > best_m:
                best_m = extended
            stack.append((previous, extended, path | {previous}))
        if not extended_any:
            candidates.append((distance, current))
    candidates.sort(key=lambda entry: (-entry[0], entry[1]))
    terminus = candidates[0][1] if candidates else start_link_id
    return best_m, terminus, False


def derive_v4_boundary_attribution(
    code: str,
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
) -> dict[str, Any]:
    """Unit #39: did the REQUESTED MODEL BOUNDARY cause this runway shortage?

    A ``v4_lane_drop_without_runway`` finding says a lane drop's upstream
    corridor holds less road than the file's own lane-change distance needs.
    That shortage has two very different causes, and until this unit the
    refusal could only say it had no evidence which: the corridor's upstream
    end is a node THIS GENERATOR synthesized on the requested extract
    boundary (the lasso cut a road that continues in reality — the shortage
    is the MODEL's edge), or the corridor genuinely ends inside the model
    (the shortage is the road's). This deriver measures which, and how much
    road the job's own stored source declares beyond the boundary when the
    boundary did it.

    Known conservatism, measured by this unit's adversarial pass: the
    beyond-boundary walk matches a continuing road by way id first and then
    by EXACT name, so a Korean name written with a spacing variant
    (``둔산로31번길`` vs ``둔산로 31번길``) under-matches and the walk stops
    early. That can only SHORTEN a declared continuation, never invent one,
    and it cannot flip the ``status`` — a shortage attributed to the boundary
    stays attributed. Whoever builds the extension proposal (unit #40) reads
    ``source_length_available_m`` as a floor, not a survey.

    Returns ``{"boundary_attribution": {...} | None, "provenance": {...}}``,
    mirroring :func:`derive_v3_source_testimony`'s shape. ``provenance``
    always names its sources (``source_artifact`` / ``source_field`` /
    ``link_id`` / ``job_id``) plus the re-trace's own measurement
    (``traced_available_m`` / ``terminus_link_id`` /
    ``terminus_node_osm_id`` once traced); when no attribution could be
    derived it adds ``reason_code`` + ``detail``.

    The measurement, in the order it is taken:

    1. **The judge's numbers are the anchor.** ``required_m``/``available_m``
       come from the finding's own context — the gate already measured the
       deficit and this deriver never re-litigates it. The corridor is then
       RE-TRACED over the draft census with the judge's own walk
       (:func:`_draft_upstream_runway_terminus` — longest simple feeding
       path, link lengths only, the judge's own search budget) purely to
       learn WHERE the best runway runs out, and the re-traced distance must
       agree with the judge's ``available_m`` within
       :data:`_V4_TRACE_TOLERANCE_M`; a disagreement is reported by name
       (``trace_disagrees_with_judge``), never resolved by trusting either
       side silently.
    2. **The corridor's upstream terminus is classified by the census's own
       boundary markers.** The terminus is the best path's upstream-most
       link's ENTRY node (``from_node_osm_id``). It is the boundary when
       ``metadata['boundary']['way_trims']`` records it as a
       ``synthesized_node_id`` (the generator cut a source way there and put
       this node ON the requested boundary — the primary marker, and the one
       the pre-#39 check could never see because it read only the
       DEPARTURE-side dangling census), or when a
       ``dangling_approaches`` entry with cause ``model_boundary_correct``
       names the same node (the reverse carriageway's witness for an
       untrimmed boundary end). No marker → interior.
    3. **Boundary → the SOURCE testifies about the road beyond.** Through
       unit #36's own cached index (:func:`_source_osm_index`, same cache),
       starting from the trim's own measured ``metres_cut`` (the road the
       generator itself removed — census evidence, named as such), the walk
       continues upstream along ways that ARRIVE at each stop, following the
       SAME way id and then the same road name (a junction does not end a
       road that continues through it; a differently named road is not this
       corridor continuing). It stops at the deficit, at a name change, or
       at the extract's own edge. ``continuation_evidence`` then carries
       ``source_length_available_m`` (+ ``meets_deficit``), and — when the
       walk ran out at a node OUTSIDE the requested region —
       ``continuation_beyond_extract``: an extract cut at the same lasso
       cannot testify past its own edge, which is itself boundary-attribution
       evidence that the MODEL's edge (not the road) ends there.
    4. **Interior → the honest opposite**, with the source consulted the
       same way: ``source_confirms`` records whether the source declares no
       drivable arrival at the terminus either (the world agrees the
       corridor ends here), declares upstream ways the draft lost
       (``source_upstream_way_ids``), could not name the node at all, or
       could not be read.

    The named "no attribution" reasons, in the order they are measured:
    ``judge_measurement_missing`` (the finding carries no usable
    required/available pair), ``no_draft_record`` / ``no_draft_census``
    (the finding maps to no census link), ``trace_budget_exhausted``,
    ``trace_disagrees_with_judge``, ``trim_way_mismatch`` (the trim at the
    terminus names a different way than the corridor's — conflicting census
    evidence is reported, not adjudicated), ``way_not_in_source``, and
    ``source_artifact_missing`` (boundary marked, but neither a trim
    measurement nor a readable source exists to state any continuation).

    Evidence for the registry's refusal rationale
    (``services.agent.genesis_fixes._build_v4_lane_drop_without_runway``),
    NEVER a tier change: whichever way the attribution lands there is still
    no automated fix — the boundary-extension treatment is unit #40's.
    """
    provenance: dict[str, Any] = {
        "source_artifact": "genesis_json+source_osm",
        "source_field": _V4_ATTRIBUTION_SOURCE_FIELD,
        "link_id": None,
        "job_id": job_id,
    }

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "boundary_attribution": None,
            "provenance": {**provenance, "reason_code": reason_code, "detail": detail},
        }

    context = dict(finding.get("context") or {})
    try:
        required_m = float(context["required_m"])
        available_m = float(context["available_m"])
    except (KeyError, TypeError, ValueError):
        return _nothing(
            "judge_measurement_missing",
            "the finding's context carries no usable required_m/available_m pair -- the "
            "judge's own deficit is the anchor this attribution starts from",
        )
    deficit_m = round(required_m - available_m, 2)
    if deficit_m <= 0:
        return _nothing(
            "judge_measurement_missing",
            f"required_m {required_m} does not exceed available_m {available_m} -- there is "
            "no deficit to attribute",
        )
    provenance["deficit_m"] = deficit_m

    subject_head = str(finding.get("subject") or "").strip().split(" (", 1)[0].strip()
    try:
        link_id = int(subject_head)
    except (TypeError, ValueError):
        return _nothing("no_draft_record", "the finding names no addressable link id")
    provenance["link_id"] = str(link_id)

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    link = draft.link_by_id(link_id)
    if link is None:
        return _nothing(
            "no_draft_record",
            f"the draft census declares no links[] record for id {link_id}",
        )

    budget = GateSettings().lane_change_search_budget
    traced_m, terminus_link_id, exhausted = _draft_upstream_runway_terminus(
        draft, link_id, required_m, budget
    )
    provenance["traced_available_m"] = round(traced_m, 2)
    if exhausted or terminus_link_id is None:
        return _nothing(
            "trace_budget_exhausted",
            f"the upstream re-trace exhausted the judge's own search budget ({budget}) "
            "before the corridor's end could be established",
        )
    if abs(traced_m - available_m) > _V4_TRACE_TOLERANCE_M:
        return _nothing(
            "trace_disagrees_with_judge",
            f"the census re-trace measured {round(traced_m, 2)} m of upstream runway where "
            f"the judge's verdict recorded {available_m} m -- the two measurements disagree, "
            "so no terminus is trusted and no attribution is derived",
        )
    terminus_link = draft.link_by_id(int(terminus_link_id))
    terminus_node = int(terminus_link.from_node_osm_id)
    provenance["terminus_link_id"] = str(terminus_link_id)
    provenance["terminus_node_osm_id"] = terminus_node

    metadata = draft.metadata or {}
    boundary_meta = metadata.get("boundary")
    boundary_meta = boundary_meta if isinstance(boundary_meta, Mapping) else {}
    trim_entry: dict[str, Any] | None = None
    for entry in boundary_meta.get("way_trims") or []:
        if not isinstance(entry, Mapping) or entry.get("synthesized_node_id") is None:
            continue
        try:
            if int(str(entry.get("synthesized_node_id"))) == terminus_node:
                trim_entry = dict(entry)
                break
        except (TypeError, ValueError):
            continue
    dangling_marker: dict[str, Any] | None = None
    if trim_entry is None:
        connectivity = metadata.get("connectivity")
        if isinstance(connectivity, Mapping):
            for entry in connectivity.get("dangling_approaches") or []:
                if not isinstance(entry, Mapping) or entry.get("cause") != DANGLING_MODEL_BOUNDARY:
                    continue
                try:
                    if int(str(entry.get("node_osm_id"))) == terminus_node:
                        dangling_marker = dict(entry)
                        break
                except (TypeError, ValueError):
                    continue

    corridor_way_id = int(terminus_link.osm_way_id)
    index = _source_osm_index(job_id) if job_id else None

    # ---- no boundary marker: the corridor ends INSIDE the model ------------
    if trim_entry is None and dangling_marker is None:
        provenance["way_id"] = corridor_way_id
        attribution: dict[str, Any] = {
            "status": "interior",
            "terminus_node": terminus_node,
            "deficit_m": deficit_m,
        }
        if index is None:
            attribution["source_confirms"] = None
            attribution["reason"] = (
                f"the corridor's upstream end (node {terminus_node}) carries no boundary "
                "marker in the census, and the job's source .osm is absent or unreadable, "
                "so whether the road continues there could not be confirmed"
            )
        elif terminus_node not in index["node_ways"] and terminus_node not in index["node_latlon"]:
            attribution["source_confirms"] = None
            attribution["reason"] = (
                f"the corridor's upstream end (node {terminus_node}) carries no boundary "
                "marker in the census, and the source holds no drivable way touching that "
                "node id, so the source could not be asked about it"
            )
        else:
            arriving: list[int] = []
            for candidate_id in index["node_ways"].get(terminus_node, ()):
                node_refs, oneway = index["ways"][candidate_id]
                travel = bool(terminus_link.forward) if candidate_id == corridor_way_id else None
                if _way_arrives_node(node_refs, oneway, terminus_node, travel_forward=travel):
                    arriving.append(candidate_id)
            if arriving:
                attribution["source_confirms"] = False
                attribution["source_upstream_way_ids"] = sorted(arriving)
                attribution["reason"] = (
                    f"the corridor ends inside the model at node {terminus_node}, but the "
                    f"source declares drivable way(s) {', '.join(str(w) for w in sorted(arriving))} "
                    "arriving there -- an upstream continuation the draft does not carry; "
                    "either way, the shortage is not the requested boundary's"
                )
            else:
                attribution["source_confirms"] = True
                attribution["reason"] = (
                    f"the corridor ends inside the model at node {terminus_node} and the "
                    "source declares no drivable way arriving there either -- the road "
                    "genuinely ends here; the deficit is the road's, not the boundary's"
                )
        return {"boundary_attribution": attribution, "provenance": dict(provenance)}

    # ---- boundary marker found ---------------------------------------------
    marker = "way_trim" if trim_entry is not None else "dangling_approach"
    provenance["boundary_marker"] = marker
    provenance["boundary_node"] = terminus_node
    if trim_entry is not None:
        try:
            trim_way_id = int(str(trim_entry.get("way_id")))
        except (TypeError, ValueError):
            trim_way_id = corridor_way_id
        if trim_way_id != corridor_way_id:
            return _nothing(
                "trim_way_mismatch",
                f"the trim record at node {terminus_node} names way {trim_way_id}, but the "
                f"corridor's terminus link {terminus_link_id} carries way {corridor_way_id} "
                "-- conflicting census evidence is reported, not adjudicated",
            )
    way_id = corridor_way_id
    provenance["way_id"] = way_id

    metres_cut = 0.0
    if trim_entry is not None:
        try:
            metres_cut = max(float(trim_entry.get("metres_cut") or 0.0), 0.0)
        except (TypeError, ValueError):
            metres_cut = 0.0

    continuation: dict[str, Any]
    if index is None:
        if trim_entry is None or metres_cut <= 0:
            return _nothing(
                "source_artifact_missing",
                f"the corridor ends at boundary node {terminus_node}, but the job's source "
                ".osm is absent or unreadable and the census records no trim measurement "
                "there -- no continuation beyond the boundary can be stated",
            )
        continuation = {
            "source_length_available_m": round(metres_cut, 2),
            "meets_deficit": metres_cut >= deficit_m,
            "measured_from": "way_trim_record",
        }
    else:
        if way_id not in index["ways"]:
            return _nothing(
                "way_not_in_source",
                f"the census names way {way_id} for the corridor's terminus link, but the "
                "source holds no drivable way with that id",
            )
        node_refs, _oneway = index["ways"][way_id]
        if trim_entry is not None:
            visited = {way_id}
            current_node = node_refs[0] if str(trim_entry.get("end") or "start") == "start" else node_refs[-1]
        else:
            visited = set()
            current_node = terminus_node
        accumulated = metres_cut
        road_name = index["way_names"].get(way_id, "")
        walked: list[int] = []
        other_arrivals: list[int] = []
        hops = 0
        while accumulated < deficit_m and hops < _V4_SOURCE_WALK_MAX_WAYS:
            hops += 1
            best_step: tuple[float, int, int] | None = None
            others: list[int] = []
            for candidate_id in index["node_ways"].get(current_node, ()):
                if candidate_id in visited:
                    continue
                cand_refs, cand_oneway = index["ways"][candidate_id]
                travel = bool(terminus_link.forward) if candidate_id == way_id else None
                run = _arriving_run(
                    cand_refs, cand_oneway, current_node, index["node_latlon"], travel_forward=travel
                )
                if run is None:
                    continue
                same_road = candidate_id == way_id or (
                    bool(road_name) and index["way_names"].get(candidate_id, "") == road_name
                )
                if not same_road:
                    others.append(candidate_id)
                    continue
                if (
                    best_step is None
                    or run[0] > best_step[0]
                    or (run[0] == best_step[0] and candidate_id < best_step[2])
                ):
                    best_step = (run[0], run[1], candidate_id)
            if best_step is None:
                other_arrivals = sorted(others)
                break
            accumulated += best_step[0]
            visited.add(best_step[2])
            walked.append(best_step[2])
            current_node = best_step[1]

        meets = accumulated >= deficit_m
        continuation = {
            "source_length_available_m": round(accumulated, 2),
            "meets_deficit": meets,
            "measured_from": (
                "way_trim_record+source_osm" if trim_entry is not None else "source_osm"
            ),
        }
        if walked:
            continuation["continuation_way_ids"] = walked
        if other_arrivals:
            continuation["other_arriving_way_ids"] = other_arrivals
        if not meets:
            lat_lon = index["node_latlon"].get(current_node)
            inside = (
                _point_in_requested_region(lat_lon[0], lat_lon[1], boundary_meta)
                if lat_lon is not None
                else None
            )
            if inside is False:
                # The walk ran out at a point OUTSIDE the requested region: an
                # extract fetched with that same region cannot testify past
                # its own edge, so the absence of onward road there is the
                # EXTRACT's edge, not the road's end -- named, never inferred
                # into a length.
                continuation["continuation_beyond_extract"] = True
                continuation["extract_edge_node"] = current_node
            else:
                continuation["road_ends_in_source_at_node"] = current_node

    attribution = {
        "status": "boundary_attributed",
        "way_id": way_id,
        "boundary_node": terminus_node,
        "deficit_m": deficit_m,
        "boundary_marker": marker,
        "continuation_evidence": continuation,
    }
    if trim_entry is not None:
        attribution["way_trim"] = trim_entry
    return {"boundary_attribution": attribution, "provenance": dict(provenance)}


# ---------------------------------------------------------------------------
# Unit #40 -- the TREATMENT for a boundary-attributed runway shortage: the
# lasso that would close it.
#
# THIS IS NOT A REGENERATION, and the distinction is the whole design. Unit
# #38's :func:`create_regenerate_job` re-derives an artifact from the SAME
# source bytes through the corrected converter, sha-verified, because the
# defect it treats is in the CONVERTER. A runway shortage attributed to the
# boundary is the opposite case: the road that would close it is not in the
# stored ``source.osm`` at all -- unit #39 measures exactly that, and on the
# motivating real job it lands as ``continuation_beyond_extract`` (the extract
# is itself clipped at the same lasso and cannot testify past its own edge).
# Re-running the same bytes would reproduce the same shortage exactly.
# Closing it needs a NEW EXTRACT of a WIDER region, i.e. a NEW GENERATION.
#
# So what this derives is a REGION, not a job: the user's own requested
# polygon with a finger spliced into it, handed to the region page's editable
# lasso where the human sees it over the basemap and presses that page's own
# 추출 button. Nothing here starts anything, nothing here touches the parent.
# ---------------------------------------------------------------------------

#: How far past the measured deficit the proposed finger reaches. A ROUND 20%,
#: named as round rather than dressed up as a measurement -- the deficit itself
#: is the gate's own number and needs no help, but a finger measured to exactly
#: the deficit can still come back short: the re-extract synthesizes a NEW
#: boundary node on the NEW ring, the corridor crosses that ring at whatever
#: angle it crosses, and the new draft re-decomposes the way into links at its
#: own node merges. 20% is a chosen overshoot, and it is reported as
#: ``margin_m`` beside ``deficit_m`` precisely so a reader can disagree with it
#: by dragging a vertex.
_V4_EXTENSION_MARGIN_FRACTION = 0.20

#: The one sentence every extension carries as ``provenance['basis']``.
_V4_EXTENSION_MARGIN_BASIS = (
    "deficit + 20% (a round overshoot, named as round -- the re-extract cuts its own "
    "boundary node on the new ring and the new draft re-decomposes the way, so the finger "
    "is drawn past the measured deficit rather than exactly onto it)"
)

#: Where an extension's facts may ever come from, recorded in every
#: derivation's provenance the way :data:`_V4_ATTRIBUTION_SOURCE_FIELD` is.
_V4_EXTENSION_SOURCE_FIELD = (
    "boundary_attribution + metadata.boundary.requested_polygon_wgs84 + way/nd"
)

#: The sharpest centreline bend the finger's two offset walls may miter around
#: before the patch is refused rather than emitted. A miter longer than twice
#: the half-width is a turn past ~120 deg, where the outer wall overshoots far
#: enough that the emitted shape stops resembling the corridor it claims to
#: cover -- and a shape a reader cannot recognise as their road is worse than
#: a named refusal.
_V4_EXTENSION_MITER_LIMIT = 2.0

# The centreline's own simplification tolerance is NOT declared here: it is
# ``project.MAX_PLANIMETRIC_ERROR_M``, imported at the point of use below —
# imported behaviour rather than a copied number, the same discipline
# ``osm_fetch.MAX_REGION_HALF_EXTENT_M`` states for the radius it borrows. A
# bend under the projection's own planimetric budget is not a bend this
# geometry can honestly claim, and every centreline vertex kept becomes TWO
# vertices on a ring with a 64-corner cap.


def _draft_requested_ring(
    boundary_meta: Mapping[str, Any],
) -> tuple[tuple[float, float], ...] | None:
    """The REQUESTED polygon the census recorded, as a ``(lat, lon)`` ring --
    or ``None`` when the job recorded no polygon (a rectangle region, or a
    census predating the field). Same field
    :func:`_point_in_requested_region` judges against, read the same way."""
    polygon = boundary_meta.get("requested_polygon_wgs84")
    if not isinstance(polygon, list) or len(polygon) < 3:
        return None
    try:
        return tuple((float(point["lat"]), float(point["lon"])) for point in polygon)
    except (KeyError, TypeError, ValueError):
        return None


def _way_path_outward(
    node_ids: tuple[int, ...], from_node: int, far_node: int
) -> list[int]:
    """The node refs of ONE way between ``from_node`` (exclusive) and
    ``far_node`` (inclusive), walking OUTWARD -- i.e. the reverse of the
    arrival run :func:`_arriving_run` measured. Empty when ``far_node`` is not
    one of the way's two ends, which is the only shape ``_arriving_run``
    returns."""
    positions = [i for i, ref in enumerate(node_ids) if ref == from_node]
    if not positions:
        return []
    if far_node == node_ids[0]:
        return list(reversed(node_ids[: max(positions)]))
    if far_node == node_ids[-1]:
        return list(node_ids[min(positions) + 1 :])
    return []


def _source_corridor_walk(
    index: Mapping[str, Any],
    *,
    outer_node: int,
    corridor_way_id: int,
    road_name: str,
    travel_forward: bool,
    visited: set[int],
    remaining_m: float,
) -> tuple[list[tuple[float, float]], list[int]]:
    """The SOURCE's own coordinates for the road continuing outward from
    ``outer_node`` -- unit #39's continuation walk, returning GEOMETRY instead
    of a length.

    Same rules, deliberately, so the two can never disagree about which road
    continues: a way must ARRIVE at the current stop
    (:func:`_arriving_run`, the finding's own travel direction for its own
    way), it must be the same way id or carry the same road NAME (a junction
    does not end a road that continues through it; a differently named road is
    not this corridor), and the longest arrival run wins with ties resolved to
    the smallest way id. The walk stops when ``remaining_m`` is covered, when
    no same-road way arrives, or at :data:`_V4_SOURCE_WALK_MAX_WAYS` hops.

    Inherits #39's own recorded conservatism verbatim: a name written with a
    spacing variant under-matches and the walk stops early. That can only
    SHORTEN the measured continuation, and what a short continuation costs
    here is honest -- the finger's remaining length is drawn on the last
    MEASURED bearing and reported as extrapolated, never claimed as source.
    """
    points: list[tuple[float, float]] = []
    walked: list[int] = []
    node_latlon = index["node_latlon"]
    current = outer_node
    accumulated = 0.0
    previous = node_latlon.get(outer_node)
    hops = 0
    while accumulated < remaining_m and hops < _V4_SOURCE_WALK_MAX_WAYS:
        hops += 1
        best: tuple[float, int, int] | None = None
        for candidate_id in index["node_ways"].get(current, ()):
            if candidate_id in visited:
                continue
            cand_refs, cand_oneway = index["ways"][candidate_id]
            travel = travel_forward if candidate_id == corridor_way_id else None
            run = _arriving_run(
                cand_refs, cand_oneway, current, node_latlon, travel_forward=travel
            )
            if run is None:
                continue
            same_road = candidate_id == corridor_way_id or (
                bool(road_name) and index["way_names"].get(candidate_id, "") == road_name
            )
            if not same_road:
                continue
            if (
                best is None
                or run[0] > best[0]
                or (run[0] == best[0] and candidate_id < best[2])
            ):
                best = (run[0], run[1], candidate_id)
        if best is None:
            break
        path = _way_path_outward(index["ways"][best[2]][0], current, best[1])
        if not path:
            break
        visited.add(best[2])
        walked.append(best[2])
        for ref in path:
            latlon = node_latlon.get(ref)
            if latlon is None:
                break
            accumulated += _latlon_metres(previous, latlon)
            points.append(latlon)
            previous = latlon
        current = best[1]
    return points, walked


def _chain_lengths(points: list[tuple[float, float]]) -> list[float]:
    """Cumulative planar lengths along a projected chain, ``[0.0, ...]``."""
    out = [0.0]
    for index in range(1, len(points)):
        (x_a, y_a), (x_b, y_b) = points[index - 1], points[index]
        out.append(out[-1] + math.hypot(x_b - x_a, y_b - y_a))
    return out


def _first_ring_exit(
    chain: list[tuple[float, float]], ring: list[tuple[float, float]]
) -> tuple[int, int, tuple[float, float]] | None:
    """Where a projected chain FIRST leaves a projected ring.

    Returns ``(chain_segment_index, ring_edge_index, crossing_point)`` for the
    smallest ``(segment, t)`` at which the chain meets the ring, or ``None``
    when it never does. A chain that STARTS on the ring (the trim's own
    synthesized terminal does exactly that) reports ``t = 0`` on the edge it
    starts on, which is the answer that unit wants: the mouth of the finger
    belongs on the edge the boundary node was cut into.

    ``None`` is also returned when two ring edges are met at the same point --
    the chain leaves through a ring VERTEX, where a finger's mouth would
    straddle two edges and there is no single edge to splice into. The caller
    names that refusal rather than picking one of the two.
    """
    ring_count = len(ring)
    for segment in range(len(chain) - 1):
        (ax, ay), (bx, by) = chain[segment], chain[segment + 1]
        dx, dy = bx - ax, by - ay
        hits: list[tuple[float, int, tuple[float, float]]] = []
        for edge in range(ring_count):
            (ex, ey) = ring[edge]
            (fx, fy) = ring[(edge + 1) % ring_count]
            sx, sy = fx - ex, fy - ey
            denom = dx * sy - dy * sx
            if denom == 0.0:
                continue
            qx, qy = ex - ax, ey - ay
            t = (qx * sy - qy * sx) / denom
            u = (qx * dy - qy * dx) / denom
            if t < -1e-9 or t > 1.0 + 1e-9:
                continue
            if u < -1e-9 or u > 1.0 + 1e-9:
                continue
            t = min(max(t, 0.0), 1.0)
            hits.append((t, edge, (ax + t * dx, ay + t * dy)))
        if not hits:
            continue
        hits.sort(key=lambda hit: (hit[0], hit[1]))
        first_t = hits[0][0]
        tied = [hit for hit in hits if abs(hit[0] - first_t) <= 1e-9]
        if len(tied) > 1:
            return None
        return segment, hits[0][1], hits[0][2]
    return None


def _simplify_chain(
    points: list[tuple[float, float]], tolerance_m: float
) -> list[tuple[float, float]]:
    """Drop chain vertices whose perpendicular deviation from the line between
    their surviving neighbours is under ``tolerance_m``. Ends are always kept.

    Not cosmetic: every centreline vertex becomes TWO ring vertices, and the
    ring the region page will accept has a 64-corner cap.
    """
    if len(points) <= 2:
        return list(points)
    kept = [points[0]]
    for index in range(1, len(points) - 1):
        (ax, ay) = kept[-1]
        (bx, by) = points[index]
        (cx, cy) = points[index + 1]
        span = math.hypot(cx - ax, cy - ay)
        if span <= 0.0:
            continue
        deviation = abs((cx - ax) * (ay - by) - (ax - bx) * (cy - ay)) / span
        if deviation >= tolerance_m:
            kept.append(points[index])
    kept.append(points[-1])
    return kept


def _corridor_walls(
    chain: list[tuple[float, float]], half_width_m: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], float] | None:
    """The two offset walls of a corridor of constant half-width.

    Returns ``(left, right, worst_miter)`` with one point per chain vertex on
    each side, mitered at interior vertices, or ``None`` when a bend needs a
    miter past :data:`_V4_EXTENSION_MITER_LIMIT` (the caller refuses by name).
    """
    count = len(chain)
    normals: list[tuple[float, float]] = []
    for index in range(count - 1):
        (ax, ay), (bx, by) = chain[index], chain[index + 1]
        length = math.hypot(bx - ax, by - ay)
        if length <= 0.0:
            return None
        normals.append((-(by - ay) / length, (bx - ax) / length))
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    worst = 1.0
    for index, (x, y) in enumerate(chain):
        if index == 0:
            nx, ny = normals[0]
            scale = 1.0
        elif index == count - 1:
            nx, ny = normals[-1]
            scale = 1.0
        else:
            (px, py), (qx, qy) = normals[index - 1], normals[index]
            mx, my = px + qx, py + qy
            length = math.hypot(mx, my)
            if length <= 0.0:
                return None
            mx, my = mx / length, my / length
            projection = mx * px + my * py
            if projection <= 0.0:
                return None
            scale = 1.0 / projection
            if scale > _V4_EXTENSION_MITER_LIMIT:
                return None
            worst = max(worst, scale)
            nx, ny = mx, my
        left.append((x + nx * half_width_m * scale, y + ny * half_width_m * scale))
        right.append((x - nx * half_width_m * scale, y - ny * half_width_m * scale))
    return left, right, worst


# ---------------------------------------------------------------------------
# Unit #48 -- the SAME finger, derived once and reusable.
#
# #40 derived exactly one finger and spliced it in the same breath. Two things
# unit #48 needs that it could not give: a SECOND boundary finding (a
# ``v3_orphan_link`` whose way was cut at both ends) wants two fingers on one
# ring, and the "treat every boundary finding at once" proposal wants every
# job's fingers on one ring. So the derivation is split in two -- the geometry
# of ONE finger (:func:`_boundary_patch_from_corridor`) and the splice of N of
# them into one ring (:func:`_splice_patches_into_ring`) -- with #40's own body
# rewritten to call both. NOT a reimplementation: every rule below (the source
# walk, the extrapolated remainder on the last measured bearing, the miter
# limit, the anti-hull test, the region kernel's own verdict) is #40's, moved
# rather than restated, so a fix to either lands on both callers at once.
# ---------------------------------------------------------------------------


def _boundary_patch_from_corridor(
    *,
    index: Mapping[str, Any],
    plane: Any,
    ring_wgs84: tuple[tuple[float, float], ...],
    ring_xy: list[tuple[float, float]],
    way_id: int,
    boundary_node: int,
    trim: Mapping[str, Any] | None,
    corridor_link: Any,
    needed_m: float,
    margin_m: float,
) -> dict[str, Any]:
    """ONE finger, in the plane the caller built -- unit #40's steps 2, 4 and
    5, extracted verbatim so a second caller cannot re-derive them differently.

    Returns either ``{"reason_code": ..., "detail": ...}`` (every one of #40's
    own named per-corridor refusals) or the patch::

        {"edge_index", "first_u", "second_u", "patch_wgs84", "crossing_xy",
         "centreline_xy", "length_m", "measured_source_m", "extrapolated_m",
         "half_width_m", "roadway_width_m", "continuation_way_ids",
         "vertices_added"}

    ``first_u``/``second_u`` are the mouth's two positions along the crossing
    ring EDGE, as fractions of that edge from its own first end. They are what
    lets a caller splicing SEVERAL patches order them along a shared edge and
    NAME an overlap instead of emitting a ring whose fingers grow through each
    other -- the one fact #40 never had to compute, because one finger cannot
    collide with itself.

    The patch's own vertices are already checked against the anti-hull rule
    here (every vertex but the two mouth points lies OUTSIDE the ring the user
    drew), because that test is about ONE finger and the user's ORIGINAL shape
    -- it does not become a different question when a second finger exists.
    """
    from services.genesis.osm import _inside_polygon  # noqa: PLC0415
    from services.genesis.project import MAX_PLANIMETRIC_ERROR_M  # noqa: PLC0415

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {"reason_code": reason_code, "detail": detail}

    node_refs, _oneway = index["ways"][way_id]
    node_latlon = index["node_latlon"]
    visited: set[int] = set()
    chain_wgs84: list[tuple[float, float]] = []
    if trim is not None:
        try:
            nodes_cut = int(trim.get("nodes_cut") or 0)
        except (TypeError, ValueError):
            nodes_cut = 0
        trim_end = str(trim.get("end") or "start")
        if nodes_cut <= 0 or nodes_cut >= len(node_refs):
            return _nothing(
                "trim_record_unusable",
                f"the trim record at node {boundary_node} cut {nodes_cut} of way {way_id}'s "
                f"{len(node_refs)} node(s) -- there is no kept node left to start the "
                "corridor's outward walk from",
            )
        # The walk starts at the last node the trim KEPT (inside the region) and
        # steps outward through the nodes it cut. The ring crossing between
        # those two is where the generator synthesized the boundary terminal,
        # recomputed below from the ring itself rather than read off an offset
        # carriageway's first point.
        if trim_end == "start":
            seed = [node_refs[nodes_cut]] + list(reversed(node_refs[:nodes_cut]))
            outer_node = node_refs[0]
        else:
            seed = [node_refs[len(node_refs) - nodes_cut - 1]] + list(
                node_refs[len(node_refs) - nodes_cut :]
            )
            outer_node = node_refs[-1]
        visited.add(way_id)
        for ref in seed:
            latlon = node_latlon.get(ref)
            if latlon is None:
                break
            chain_wgs84.append(latlon)
    else:
        # An UNTRIMMED boundary end (the dangling-approach witness): the
        # boundary node is a real source node, and everything beyond it is
        # found by the same continuation walk.
        latlon = node_latlon.get(boundary_node)
        if latlon is None:
            return _nothing(
                "no_measured_continuation_direction",
                f"the source holds no coordinate for boundary node {boundary_node}, so the "
                "corridor's outward direction could not be measured",
            )
        chain_wgs84.append(latlon)
        outer_node = boundary_node

    walked_points, walked_ways = _source_corridor_walk(
        index,
        outer_node=outer_node,
        corridor_way_id=way_id,
        road_name=index["way_names"].get(way_id, ""),
        travel_forward=bool(corridor_link.forward),
        visited=visited,
        # Deliberately generous: the walk is measured from the boundary, while
        # the length that matters is measured from the ring crossing, which the
        # projection below places somewhere along the first segment.
        remaining_m=needed_m,
    )
    chain_wgs84.extend(walked_points)
    if len(chain_wgs84) < 2:
        return _nothing(
            "no_measured_continuation_direction",
            "the source declares no node beyond the boundary on this corridor, so the "
            "direction to extend in could only be guessed",
        )

    chain_xy = [plane.forward(lat, lon) for lat, lon in chain_wgs84]
    if not _inside_polygon(chain_wgs84[0][0], chain_wgs84[0][1], ring_wgs84):
        return _nothing(
            "corridor_start_outside_requested_region",
            "the corridor's inward-most node beyond the boundary already lies outside the "
            "requested polygon -- conflicting census evidence, reported rather than "
            "adjudicated",
        )
    if all(_inside_polygon(lat, lon, ring_wgs84) for lat, lon in chain_wgs84):
        return _nothing(
            "corridor_does_not_leave_requested_region",
            "every node the source declares on this corridor lies inside the requested "
            "polygon, so no road beyond the region was measured to extend toward",
        )
    exit_hit = _first_ring_exit(chain_xy, ring_xy)
    if exit_hit is None:
        return _nothing(
            "crossing_edge_unidentifiable",
            "the corridor leaves the requested ring through a CORNER (two edges meet at the "
            "one crossing point), or its exit solved on no edge at all -- either way there is "
            "no single edge to open a finger onto, and a splice that straddled two edges of "
            "the user's own shape would not be their shape any more",
        )
    segment_index, edge_index, crossing_xy = exit_hit

    # ---- the patch centreline, from the crossing outward -------------------
    outward = [crossing_xy] + chain_xy[segment_index + 1 :]
    if len(outward) < 2:
        # The crossing landed on the chain's LAST segment: the direction is
        # that segment's own, measured, and everything past it is extrapolated.
        (ax, ay), (bx, by) = chain_xy[segment_index], chain_xy[segment_index + 1]
        length = math.hypot(bx - ax, by - ay)
        if length <= 0.0:  # pragma: no cover - a duplicated source node
            return _nothing(
                "no_measured_continuation_direction",
                "the corridor's last measured segment has zero length",
            )
        outward = [crossing_xy, (bx, by)]
    cumulative = _chain_lengths(outward)
    measured_m = cumulative[-1]
    extrapolated_m = 0.0
    if measured_m >= needed_m:
        # Cut the centreline exactly at the needed length: the finger reaches
        # the deficit plus its margin and no further.
        for index_ in range(1, len(outward)):
            if cumulative[index_] >= needed_m:
                over = cumulative[index_] - needed_m
                (ax, ay), (bx, by) = outward[index_ - 1], outward[index_]
                segment_len = cumulative[index_] - cumulative[index_ - 1]
                ratio = 1.0 - (over / segment_len) if segment_len > 0 else 1.0
                outward = outward[:index_] + [
                    (ax + (bx - ax) * ratio, ay + (by - ay) * ratio)
                ]
                break
        measured_m = needed_m
    else:
        # The source itself ran out (it is clipped at the same lasso). The
        # remaining metres are drawn on the LAST MEASURED BEARING and named as
        # extrapolation -- a request for data, not a claim about the road.
        (ax, ay), (bx, by) = outward[-2], outward[-1]
        length = math.hypot(bx - ax, by - ay)
        if length <= 0.0:  # pragma: no cover - a duplicated source node
            return _nothing(
                "no_measured_continuation_direction",
                "the corridor's last measured segment has zero length",
            )
        extrapolated_m = round(needed_m - measured_m, 2)
        outward.append(
            (bx + (bx - ax) / length * extrapolated_m, by + (by - ay) / length * extrapolated_m)
        )
        measured_m = round(measured_m, 2)

    centreline = _simplify_chain(outward, MAX_PLANIMETRIC_ERROR_M)
    roadway_width_m = round(float(corridor_link.lanes) * float(corridor_link.lane_width_m), 2)
    half_width_m = round(roadway_width_m / 2.0 + margin_m, 2)
    walls = _corridor_walls(centreline, half_width_m)
    if walls is None:
        return _nothing(
            "corridor_bend_too_sharp",
            "the corridor bends too sharply for a constant-width finger to be mitered around "
            f"(past the {_V4_EXTENSION_MITER_LIMIT}x half-width limit) -- the emitted shape "
            "would stop resembling the road it claims to cover",
        )
    left, right, _worst_miter = walls

    # ---- the mouth: two points ON the crossing edge ------------------------
    (ex, ey) = ring_xy[edge_index]
    (fx, fy) = ring_xy[(edge_index + 1) % len(ring_xy)]
    edge_dx, edge_dy = fx - ex, fy - ey
    (c0x, c0y), (c1x, c1y) = centreline[0], centreline[1]
    dir_x, dir_y = c1x - c0x, c1y - c0y
    denom = edge_dx * dir_y - edge_dy * dir_x
    if abs(denom) <= 1e-9:
        return _nothing(
            "crossing_edge_unidentifiable",
            "the corridor runs ALONG the ring edge it meets, so the finger's mouth has no "
            "single place on that edge to sit",
        )
    mouth: dict[str, tuple[float, float, float]] = {}
    for side, (px, py) in (("left", left[0]), ("right", right[0])):
        u = ((px - ex) * dir_y - (py - ey) * dir_x) / denom
        mouth[side] = (u, ex + edge_dx * u, ey + edge_dy * u)
    if any(u < 0.0 or u > 1.0 for u, _x, _y in mouth.values()):
        edge_len = math.hypot(edge_dx, edge_dy)
        return _nothing(
            "patch_mouth_exceeds_crossing_edge",
            f"a finger {round(2.0 * half_width_m, 2)} m across does not fit on the "
            f"{round(edge_len, 2)} m ring edge it would open onto -- splicing it would cut "
            "across the neighbouring edges of the user's own shape",
        )
    first_side = "left" if mouth["left"][0] <= mouth["right"][0] else "right"
    second_side = "right" if first_side == "left" else "left"
    first_wall = left if first_side == "left" else right
    second_wall = right if first_side == "left" else left

    patch_xy: list[tuple[float, float]] = [
        (mouth[first_side][1], mouth[first_side][2]),
        *first_wall[1:],
        *reversed(second_wall[1:]),
        (mouth[second_side][1], mouth[second_side][2]),
    ]
    patch_wgs84 = [
        (round(lat, 7), round(lon, 7))
        for lat, lon in (plane.inverse(x, y) for x, y in patch_xy)
    ]

    # Anti-hull, measured: every patch vertex EXCEPT the two on the ring edge
    # itself must lie outside the shape the user drew, so the detour bulges
    # OUTWARD and the original interior is contained in the new one.
    for lat, lon in patch_wgs84[1:-1]:
        if _inside_polygon(lat, lon, ring_wgs84):
            return _nothing(
                "patch_re_enters_requested_region",
                "the proposed finger re-enters the region the user already drew, so splicing "
                "it would carve into their own shape instead of extending it",
            )

    return {
        "edge_index": edge_index,
        "first_u": mouth[first_side][0],
        "second_u": mouth[second_side][0],
        "patch_wgs84": patch_wgs84,
        "crossing_xy": crossing_xy,
        "centreline_xy": centreline,
        "length_m": needed_m,
        "measured_source_m": round(measured_m, 2),
        "extrapolated_m": extrapolated_m,
        "half_width_m": half_width_m,
        "roadway_width_m": roadway_width_m,
        "continuation_way_ids": walked_ways,
        "vertices_added": len(patch_wgs84),
    }


def _splice_patches_into_ring(
    *,
    ring_wgs84: tuple[tuple[float, float], ...],
    patches: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """N fingers into ONE ring -- unit #40's step 6, generalized.

    ``patches`` are :func:`_boundary_patch_from_corridor` results, each
    carrying the ring EDGE its mouth sits on and the two positions along that
    edge the mouth occupies. They are inserted in ring order, and within one
    edge in mouth order, so every corner the user drew survives in sequence and
    the fingers open outward between them.

    **TWO FINGERS THAT WOULD COLLIDE ARE REFUSED BY NAME, never resolved
    quietly.** Two mouths on one edge whose spans overlap (or merely touch)
    cannot both be spliced without one growing through the other's mouth, and
    the honest shapes available then -- picking one, or hulling the pair into a
    fatter finger neither corridor measured -- are both this module deciding
    something the reader never saw. So the composite refuses with
    ``patch_mouths_collide_on_edge``, naming both corridors and the overlap in
    metres, and the per-finding proposals stay exactly where they were: the
    reader takes them one at a time. (Two fingers on DIFFERENT edges that still
    manage to cross are caught by the self-intersection test below, which is
    the same test #40 already ran on one.)

    Returns ``{"reason_code", "detail"}`` or ``{"extended_polygon_wgs84",
    "extended_area_km2", "added_area_km2"}``. The ORIGINAL corners travel
    verbatim -- not re-rounded, not re-derived -- for the reason #40 states:
    this is the user's shape, and the only thing this function may add is the
    fingers.
    """
    from services.osm_fetch import (  # noqa: PLC0415
        OsmFetchRefused,
        RegionPolygon,
        _segments_cross,
        _segments_overlap,
        measure_region,
    )

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {"reason_code": reason_code, "detail": detail}

    by_edge: dict[int, list[Mapping[str, Any]]] = {}
    for patch in patches:
        by_edge.setdefault(int(patch["edge_index"]), []).append(patch)
    for edge_index, group in by_edge.items():
        group.sort(key=lambda patch: (float(patch["first_u"]), float(patch["second_u"])))
        for previous, following in zip(group, group[1:]):
            if float(following["first_u"]) > float(previous["second_u"]):
                continue
            (ex, ey) = ring_wgs84[edge_index]
            (fx, fy) = ring_wgs84[(edge_index + 1) % len(ring_wgs84)]
            edge_len = _latlon_metres((ex, ey), (fx, fy))
            overlap_m = round(
                (float(previous["second_u"]) - float(following["first_u"])) * edge_len, 2
            )
            return _nothing(
                "patch_mouths_collide_on_edge",
                f"two corridors want the same {round(edge_len, 2)} m stretch of ring edge "
                f"{edge_index + 1}: the mouths overlap by {overlap_m} m "
                f"({previous.get('label') or 'the first'} and "
                f"{following.get('label') or 'the second'}). Splicing both would grow one "
                "finger through the other's mouth, and choosing between them -- or hulling "
                "them into one fatter finger neither corridor measured -- is not this "
                "module's call. Each finding's own proposal is unaffected; take them one "
                "at a time",
            )

    extended_wgs84: list[tuple[float, float]] = []
    for index_, corner in enumerate(ring_wgs84):
        extended_wgs84.append(corner)
        for patch in by_edge.get(index_, ()):
            extended_wgs84.extend(patch["patch_wgs84"])

    count = len(extended_wgs84)
    for i in range(count):
        a, b = extended_wgs84[i], extended_wgs84[(i + 1) % count]
        for j in range(i + 1, count):
            c, d = extended_wgs84[j], extended_wgs84[(j + 1) % count]
            adjacent = j == i + 1 or (i == 0 and j == count - 1)
            if _segments_overlap(a, b, c, d) or (
                not adjacent and _segments_cross(a, b, c, d)
            ):
                return _nothing(
                    "extension_polygon_self_intersects",
                    f"the spliced ring crosses itself (edge {i + 1} meets edge {j + 1}); a "
                    "broken ring is refused rather than emitted for a human to discover on "
                    "the map",
                )
    try:
        original_region = RegionPolygon.from_sequence(list(ring_wgs84))
    except OsmFetchRefused as exc:
        return _nothing(
            "requested_polygon_invalid",
            "the region this job recorded does not validate as a polygon today "
            f"({'; '.join(exc.reasons)})",
        )
    try:
        extended_region = RegionPolygon.from_sequence(extended_wgs84)
    except OsmFetchRefused as exc:
        return _nothing(
            "extension_polygon_refused_by_region_kernel",
            "the widened shape is refused by the same constructor the fetch itself is held "
            f"to ({'; '.join(exc.reasons)})",
        )

    original_measure = measure_region(original_region)
    extended_measure = measure_region(extended_region)
    if not extended_measure.within_region_cap:
        return _nothing(
            "extension_exceeds_region_cap",
            f"the widened region measures {round(extended_measure.area_km2, 4)} km² against "
            f"the {round((2.0 * extended_measure.validity_radius_m / 1000.0) ** 2, 4)} km² "
            "limit in force at its own centre latitude (half-extent "
            f"{round(extended_measure.half_extent_m, 1)} m against a "
            f"{round(extended_measure.validity_radius_m, 1)} m validity radius) -- the same "
            "cap the region page's own extract button is held to",
        )
    return {
        "extended_polygon_wgs84": [[lat, lon] for lat, lon in extended_region.vertices],
        "extended_area_km2": round(extended_measure.area_km2, 6),
        "added_area_km2": round(
            extended_measure.area_km2 - original_measure.area_km2, 6
        ),
    }


def derive_v4_boundary_extension(
    finding: Mapping[str, Any],
    attribution: Mapping[str, Any] | None,
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
) -> dict[str, Any]:
    """Unit #40: the WIDER LASSO that would close a boundary-attributed runway
    shortage — the user's own polygon with a finger spliced into it.

    Takes unit #39's measured :func:`derive_v4_boundary_attribution` output and
    the job's own recorded requested region, and returns either a proposed
    region or a NAMED reason there is none. It starts nothing, writes nothing
    and never touches the parent job: the proposal's destination is the region
    page's editable lasso, whose own 추출 button is the ask-first gate.

    **Why a new generation and not a regeneration** (unit #38): a regeneration
    re-derives from the SAME source bytes, and the road that would close this
    shortage is not in those bytes — the stored ``source.osm`` was fetched with
    the very region being extended, so it is clipped at the same edge (which is
    what #39's ``continuation_beyond_extract`` records). Re-running it would
    reproduce the shortage exactly.

    The measurement, in the order it is taken:

    1. **Eligibility is the attribution's**, never re-litigated here. Only
       ``status == "boundary_attributed"`` is treatable: an ``interior``
       shortage is the ROAD's, and extending the region for it would be a lie
       told in the shape of a polygon (``interior_not_eligible``).
    2. **The corridor's geometry beyond the boundary is the SOURCE's own.**
       For a trim-marked boundary the walk starts at the way's last KEPT node
       (inside the region) and steps outward through the very nodes the trim
       cut, then continues through :func:`_source_corridor_walk` — #39's
       continuation rules, returning coordinates. The direction is therefore
       MEASURED at every step the source declares. When the source runs out
       before the needed length (the real link-90 case: 127.06 m declared
       against a 147.85 m deficit, because the extract is clipped too), the
       remainder is drawn on the LAST MEASURED BEARING and reported as
       ``extrapolated_m`` — never folded into the measured figure. A straight
       continuation is an honest REQUEST for data; it is not a claim about the
       road, which is exactly why the result is handed to a human on a map
       instead of being fetched.
    3. **The patch covers ``deficit_m`` plus a stated margin**
       (:data:`_V4_EXTENSION_MARGIN_BASIS`) along the corridor, and the
       corridor's own declared roadway (``lanes × lane_width_m``, census-read)
       plus that same margin on each side across it.
    4. **The splice is a FINGER, never a hull.** The patch's mouth is placed on
       the single ring edge the corridor crosses, and its vertices are INSERTED
       into the vertex ring between that edge's two ends — so every corner the
       user drew survives, in order, and area the user deliberately excluded
       stays excluded. A convex hull of the same points would silently swallow
       it. The result is checked three ways before it is returned: the ring is
       SIMPLE (the region kernel's own ``_segments_cross`` /
       ``_segments_overlap``, the same pair that judges a drawn lasso), every
       patch vertex other than the two mouth points lies OUTSIDE the original
       ring (so the detour bulges outward and the original interior is
       contained), and the whole shape passes
       :class:`services.osm_fetch.RegionPolygon` — the constructor the fetch
       itself is held to, vertex cap included.
    5. **The region cap is the region page's own**
       (:func:`services.osm_fetch.measure_region`), not a second copy: refused
       by name with BOTH numbers when the widened shape is over it.

    Returns, on success::

        {"extended_polygon_wgs84": [[lat, lon], ...],
         "added_area_km2": float, "extended_area_km2": float,
         "corridor_patch": {...}, "provenance": {...}}

    and otherwise ``{"reason_code": ..., "detail": ...}``. The named reasons:
    ``no_boundary_attribution``, ``interior_not_eligible``,
    ``attribution_incomplete``, ``no_draft_census``, ``no_draft_record``,
    ``requested_region_not_a_polygon``, ``requested_polygon_invalid``,
    ``source_artifact_missing``, ``way_not_in_source``,
    ``trim_record_unusable``, ``no_measured_continuation_direction``,
    ``corridor_start_outside_requested_region``,
    ``corridor_does_not_leave_requested_region``,
    ``crossing_edge_unidentifiable``, ``corridor_bend_too_sharp``,
    ``patch_mouth_exceeds_crossing_edge``, ``patch_re_enters_requested_region``,
    ``extension_polygon_self_intersects``,
    ``extension_polygon_refused_by_region_kernel`` and
    ``extension_exceeds_region_cap``.

    **Unit #48 rewrote the BODY and not the behaviour.** Everything from the
    corridor's outward walk to the region kernel's verdict now runs inside
    :func:`_boundary_patch_from_corridor` and :func:`_splice_patches_into_ring`,
    which unit #48's own two-ended orphan treatment and its all-at-once
    composite call as well. This function keeps its signature, its ONE-patch
    payload and every named reason above; what it no longer keeps is a private
    copy of the geometry.
    """
    from services.genesis.project import LocalTangentPlane  # noqa: PLC0415

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {"reason_code": reason_code, "detail": detail}

    # ---- 1. eligibility: the attribution's, not this function's ------------
    if not isinstance(attribution, Mapping) or not attribution:
        return _nothing(
            "no_boundary_attribution",
            "no boundary attribution was derived for this finding, so there is nothing to "
            "attribute a region extension to -- unit #39's own named reason says why",
        )
    status = str(attribution.get("status") or "")
    if status == "interior":
        return _nothing(
            "interior_not_eligible",
            "the corridor ends INSIDE the model, not at the requested boundary -- the "
            "shortage is the road's, and widening the region for it would propose an "
            "extract that cannot contain the road it claims to add",
        )
    if status != "boundary_attributed":
        return _nothing(
            "no_boundary_attribution",
            f"the attribution's status is {status or '(absent)'!r}, and only "
            "'boundary_attributed' has a region treatment",
        )
    try:
        deficit_m = float(attribution["deficit_m"])
        way_id = int(attribution["way_id"])
        boundary_node = int(attribution["boundary_node"])
    except (KeyError, TypeError, ValueError):
        return _nothing(
            "attribution_incomplete",
            "the attribution carries no usable deficit_m / way_id / boundary_node triple",
        )
    if deficit_m <= 0:
        return _nothing(
            "attribution_incomplete",
            f"the attribution's deficit_m is {deficit_m} -- there is no shortage to close",
        )

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    metadata = draft.metadata or {}
    boundary_meta = metadata.get("boundary")
    boundary_meta = boundary_meta if isinstance(boundary_meta, Mapping) else {}
    ring_wgs84 = _draft_requested_ring(boundary_meta)
    if ring_wgs84 is None:
        return _nothing(
            "requested_region_not_a_polygon",
            "the census records no requested POLYGON for this job (a rectangle region, or a "
            "census older than the field). The treatment hands a lasso to the lasso page, "
            "and rendering a rectangle as a lasso would show the user corners they never drew",
        )
    index = _source_osm_index(job_id) if job_id else None
    if index is None:
        return _nothing(
            "source_artifact_missing",
            "the job's source .osm is absent or unreadable, so the road's own direction "
            "beyond the boundary cannot be measured and would have to be guessed",
        )
    if way_id not in index["ways"]:
        return _nothing(
            "way_not_in_source",
            f"the attribution names way {way_id}, but the source holds no drivable way with "
            "that id",
        )

    # ---- 2. the corridor: the census link, then the source's coordinates ---
    corridor_links = [
        link
        for link in draft.links
        if int(link.from_node_osm_id) == boundary_node and int(link.osm_way_id) == way_id
    ]
    if not corridor_links:
        return _nothing(
            "no_draft_record",
            f"the census declares no link entering node {boundary_node} on way {way_id}, so "
            "the corridor's own roadway width could not be read",
        )
    corridor_link = min(corridor_links, key=lambda link: int(link.id))
    trim = attribution.get("way_trim")
    trim = trim if isinstance(trim, Mapping) else None

    margin_m = round(deficit_m * _V4_EXTENSION_MARGIN_FRACTION, 2)
    needed_m = round(deficit_m + margin_m, 2)

    # ---- 2-5. one plane, then ONE finger through the shared derivation -----
    # The region page's own convention: the projection is built about the
    # requested polygon's BOUNDING-BOX centre (`osm_fetch.measure_region`), so
    # the metres this splice reasons in are the metres that shape is measured
    # in. The transform is affine and separable, so a straight ring edge stays
    # a straight ring edge and a point's side of it never changes.
    lats = [lat for lat, _lon in ring_wgs84]
    lons = [lon for _lat, lon in ring_wgs84]
    plane = LocalTangentPlane(
        (min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0
    )
    ring_xy = [plane.forward(lat, lon) for lat, lon in ring_wgs84]
    patch = _boundary_patch_from_corridor(
        index=index,
        plane=plane,
        ring_wgs84=ring_wgs84,
        ring_xy=ring_xy,
        way_id=way_id,
        boundary_node=boundary_node,
        trim=trim,
        corridor_link=corridor_link,
        needed_m=needed_m,
        margin_m=margin_m,
    )
    if "reason_code" in patch:
        return dict(patch)

    # ---- 6. the splice, its simplicity, and the region kernel's verdict ----
    spliced = _splice_patches_into_ring(ring_wgs84=ring_wgs84, patches=[patch])
    if "reason_code" in spliced:
        return dict(spliced)

    return {
        **spliced,
        "corridor_patch": {
            "crossing_edge_index": patch["edge_index"],
            "crossing_point_wgs84": list(plane.inverse(*patch["crossing_xy"])),
            "centreline_wgs84": [
                list(plane.inverse(x, y)) for x, y in patch["centreline_xy"]
            ],
            "length_m": patch["length_m"],
            "measured_source_m": patch["measured_source_m"],
            "extrapolated_m": patch["extrapolated_m"],
            "half_width_m": patch["half_width_m"],
            "roadway_width_m": patch["roadway_width_m"],
            "continuation_way_ids": patch["continuation_way_ids"],
            "vertices_added": patch["vertices_added"],
        },
        "provenance": {
            "source_artifact": "genesis_json+source_osm",
            "source_field": _V4_EXTENSION_SOURCE_FIELD,
            "parent_job": job_id,
            "finding": {
                "code": str(finding.get("code") or ""),
                "subject": str(finding.get("subject") or ""),
            },
            "way_id": way_id,
            "boundary_node": boundary_node,
            "deficit_m": deficit_m,
            "margin_m": margin_m,
            "basis": _V4_EXTENSION_MARGIN_BASIS,
            "boundary_marker": str(attribution.get("boundary_marker") or ""),
            "treatment": "new_generation_from_a_wider_extract",
        },
    }


# ---------------------------------------------------------------------------
# Unit #48 -- the SECOND finding the requested boundary can cause, and the one
# the user watched survive three extensions in a row.
#
# ``v3_orphan_link``: a link no connector arrives on and none leaves. Unit #36
# could only ask the SOURCE about its departure end, and on the motivating job
# the answer was ``node_not_in_source`` -- true, unhelpful, and for the most
# boundary-shaped reason there is: the node it asked about is one THIS
# GENERATOR synthesized on the requested ring, so of course the source has
# never heard of it. The census knew the whole story in ``way_trims`` the
# entire time: way 37972527's start overhung the lasso by 3.9 m and its end by
# 5.4 m, and the two nodes cut off with those tails were the junctions at BOTH
# ends of the slip road. The link was not orphaned by the world; it was
# orphaned by the lasso, twice.
#
# So the treatment is the same treatment #40 already derives, applied per CUT
# END rather than per corridor -- one finger for each end the boundary
# severed. What is NOT extended is an orphan with no trim record: a link the
# SOURCE itself leaves unconnected is the world's answer, and dressing it as
# the boundary's would propose an extract that cannot contain the connection
# it implies. That case keeps the pure #36 refusal, with no proposal, and the
# reason is named.
#
# ``gate.py`` is untouched by all of this, deliberately: the judge's vocabulary
# does not move because the treatment lane learned a new road.
# ---------------------------------------------------------------------------

#: The ONE V3 code this boundary treatment answers for. Every other V3 code
#: keeps unit #36's pure refusal. An orphan is the only V3 finding whose whole
#: subject is ONE link with TWO ends the trim ledger can be asked about
#: end-by-end: a ``v3_*_zone`` names a component (whose boundary story, if it
#: has one, is many links long), and ``v3_silent_departure_point`` already has
#: unit #35's connector treatment and #42's dead-end one.
V3_ORPHAN_FINDING_CODE = "v3_orphan_link"

#: Where an orphan's boundary attribution may ever come from, recorded in
#: every derivation's provenance exactly as :data:`_V4_ATTRIBUTION_SOURCE_FIELD`
#: is for the runway one. The trim LEDGER is the primary witness and the source
#: ``.osm`` is the cross-check; neither alone is accepted.
_V3_ORPHAN_ATTRIBUTION_SOURCE_FIELD = "metadata.boundary.way_trims + way/nd"

#: The trim reason a severed end must carry. Imported behaviour rather than a
#: copied string -- the same discipline the extension's radius borrow states.
def _trim_tail_cut_reason() -> str:
    from services.genesis.osm import TRIM_TAIL_CUT  # noqa: PLC0415

    return TRIM_TAIL_CUT


def derive_v3_orphan_boundary_attribution(
    code: str,
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
) -> dict[str, Any]:
    """Unit #48: did the REQUESTED MODEL BOUNDARY orphan this link?

    Returns ``{"orphan_boundary_attribution": {...} | None, "provenance":
    {...}}`` -- the same shape :func:`derive_v4_boundary_attribution` returns,
    for the same reason: this is EVIDENCE for a refusal's rationale, never a
    tier change. ``CONNECTIVITY_IS_NOT_INVENTED`` holds either way; what the
    attribution buys is that the refusal can say WHOSE fault the isolation is,
    and (when it is the boundary's) that a treatment exists on another screen.

    The measurement, in the order it is taken:

    1. **The finding must name a census link.** Its ``osm_way_id`` is the way
       every question below is asked about.
    2. **The trim LEDGER is asked first**, because it is the only record that
       knows a cut happened at all: ``metadata['boundary']['way_trims']``
       entries for this way whose reason is the tail cut and which carry a
       ``synthesized_node_id``. An entry counts as THIS link's only when the
       node it synthesized is one of the link's own two terminals -- a way can
       decompose into several links, and a trim at the far end of a different
       link is not this link's boundary story.
    3. **The source CROSS-CHECKS every ledger claim**, and a disagreement is
       named rather than resolved (``trim_disagrees_with_source``). For each
       severed end the source must declare the way, must still hold the
       ``nodes_cut`` outermost node refs the ledger says were removed, and
       those refs must genuinely lie OUTSIDE the requested region while the
       first kept one lies inside -- judged by
       :func:`_point_in_requested_region`, the same kernel pair the trim itself
       cut with. A ledger nobody re-derives is a ledger that can drift.
    4. **What the severed end would restore is MEASURED, never promised.**
       The outermost cut node's own other drivable ways in the source are
       reported as ``junction_way_ids``, and the subset this census already
       carries as ``junction_way_ids_in_census``. That is the honest form of
       "this is why the link is alone": the road it met is on the other side
       of the lasso. It is not a claim that a re-extract will connect it --
       the new draft decides that, and it faces the gate on its own.

    The named "no attribution" reasons: ``no_draft_census``,
    ``no_draft_record``, ``source_artifact_missing``, ``way_not_in_source``,
    ``no_boundary_trim`` (the orphan's way was never cut at the ring -- the
    isolation is the SOURCE's, and this is the case that must NOT be offered a
    region), and ``trim_disagrees_with_source``.
    """
    provenance: dict[str, Any] = {
        "source_artifact": "genesis_json+source_osm",
        "source_field": _V3_ORPHAN_ATTRIBUTION_SOURCE_FIELD,
        "link_id": None,
        "way_id": None,
        "job_id": job_id,
    }

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {
            "orphan_boundary_attribution": None,
            "provenance": {**provenance, "reason_code": reason_code, "detail": detail},
        }

    context = dict(finding.get("context") or {})
    link_id_raw = context.get("link_id")
    if link_id_raw is None:
        subject_head = str(finding.get("subject") or "").strip().split(" (", 1)[0].strip()
        link_id_raw = subject_head or None
    try:
        link_id = int(str(link_id_raw))
    except (TypeError, ValueError):
        return _nothing("no_draft_record", "the finding names no addressable link id")
    provenance["link_id"] = str(link_id)

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    link = draft.link_by_id(link_id)
    if link is None:
        return _nothing(
            "no_draft_record",
            f"the draft census declares no links[] record for id {link_id}",
        )
    way_id = int(link.osm_way_id)
    provenance["way_id"] = way_id
    terminals = {int(link.from_node_osm_id), int(link.to_node_osm_id)}

    metadata = draft.metadata or {}
    boundary_meta = metadata.get("boundary")
    boundary_meta = boundary_meta if isinstance(boundary_meta, Mapping) else {}
    tail_cut = _trim_tail_cut_reason()
    ledger: list[dict[str, Any]] = []
    for entry in boundary_meta.get("way_trims") or []:
        if not isinstance(entry, Mapping) or entry.get("reason") != tail_cut:
            continue
        try:
            if int(str(entry.get("way_id"))) != way_id:
                continue
            synthesized = int(str(entry.get("synthesized_node_id")))
        except (TypeError, ValueError):
            continue
        if synthesized in terminals:
            ledger.append(dict(entry))
    if not ledger:
        return _nothing(
            "no_boundary_trim",
            f"the census records no boundary cut on way {way_id} that produced either of "
            f"link {link_id}'s own terminals -- nothing was severed here by the requested "
            "region, so this link's isolation is the source's and not the boundary's",
        )
    ledger.sort(key=lambda entry: str(entry.get("end") or ""))
    provenance["severed_ends"] = [str(entry.get("end") or "") for entry in ledger]

    index = _source_osm_index(job_id) if job_id else None
    if index is None:
        return _nothing(
            "source_artifact_missing",
            "this job's source .osm is absent, unreadable, unparseable, or holds no "
            "drivable ways, so the trim ledger's claim could not be cross-checked and "
            "no severed end may be stated on the ledger's word alone",
        )
    if way_id not in index["ways"]:
        return _nothing(
            "way_not_in_source",
            f"the census names way {way_id} for link {link_id}, but the source holds no "
            "drivable way with that id",
        )
    node_refs, _oneway = index["ways"][way_id]
    node_latlon = index["node_latlon"]

    ends: list[dict[str, Any]] = []
    for entry in ledger:
        end = str(entry.get("end") or "start")
        try:
            nodes_cut = int(entry.get("nodes_cut") or 0)
            metres_cut = max(float(entry.get("metres_cut") or 0.0), 0.0)
            synthesized = int(str(entry.get("synthesized_node_id")))
        except (TypeError, ValueError):
            return _nothing(
                "trim_disagrees_with_source",
                f"the trim record for way {way_id}'s {end} carries no usable nodes_cut / "
                "metres_cut / synthesized_node_id triple",
            )
        if nodes_cut <= 0 or nodes_cut >= len(node_refs):
            return _nothing(
                "trim_disagrees_with_source",
                f"the trim record says {nodes_cut} node(s) of way {way_id}'s {end} were cut, "
                f"but the source declares only {len(node_refs)} node(s) on it -- conflicting "
                "evidence is reported, not adjudicated",
            )
        if end == "start":
            cut_refs = list(node_refs[:nodes_cut])
            kept_ref = node_refs[nodes_cut]
            outer_ref = node_refs[0]
        else:
            cut_refs = list(node_refs[len(node_refs) - nodes_cut :])
            kept_ref = node_refs[len(node_refs) - nodes_cut - 1]
            outer_ref = node_refs[-1]
        placements = [
            (
                ref,
                _point_in_requested_region(*node_latlon[ref], boundary_meta)
                if ref in node_latlon
                else None,
            )
            for ref in [kept_ref, *cut_refs]
        ]
        if any(inside is None for _ref, inside in placements):
            return _nothing(
                "trim_disagrees_with_source",
                f"the source holds no coordinate (or the census records no region shape) for "
                f"at least one node of way {way_id}'s cut {end} -- the ledger's claim cannot "
                "be re-derived, so it is not repeated as though it had been",
            )
        if placements[0][1] is not True or any(inside for _ref, inside in placements[1:]):
            return _nothing(
                "trim_disagrees_with_source",
                f"re-derived against the requested region, way {way_id}'s {end} does not sit "
                "as the trim ledger describes (its last kept node must be inside and every "
                "cut node outside) -- conflicting census evidence is reported, not "
                "adjudicated",
            )
        junction_ways = sorted(
            candidate
            for candidate in index["node_ways"].get(outer_ref, ())
            if candidate != way_id
        )
        census_ways = {int(other.osm_way_id) for other in draft.links}
        ends.append(
            {
                "end": end,
                "metres_cut": round(metres_cut, 2),
                "nodes_cut": nodes_cut,
                "boundary_node": synthesized,
                "outermost_cut_node": outer_ref,
                "junction_way_ids": junction_ways,
                "junction_way_ids_in_census": [
                    candidate for candidate in junction_ways if candidate in census_ways
                ],
                "way_trim": dict(entry),
            }
        )

    attribution = {
        "status": "boundary_attributed",
        "link_id": str(link_id),
        "way_id": way_id,
        "severed_ends": ends,
        "metres_cut_total": round(sum(float(entry["metres_cut"]) for entry in ends), 2),
        "severed_end_count": len(ends),
        # A link has exactly two ends. Carried as a NUMBER beside the count above
        # so no reader of this payload -- prose builder, card, or a later unit --
        # has to assume that "severed_ends" covers the whole link.
        "link_end_count": 2,
        "reason": (
            f"link {link_id} carries way {way_id}, whose "
            + " and ".join(
                f"{entry['end']} overhung the requested region by {entry['metres_cut']} m "
                f"(node {entry['outermost_cut_node']} cut off with it"
                + (
                    f", where the source declares way(s) "
                    f"{', '.join(str(w) for w in entry['junction_way_ids'])} also meet)"
                    if entry["junction_way_ids"]
                    else ")"
                )
                for entry in ends
            )
            # Only what was measured. A junction is named where the source
            # declares one at the cut node and NOT otherwise (the outermost node
            # of a cut tail is usually a plain geometry node), and "not because
            # the road ends" is claimed only when BOTH of the link's ends were
            # severed -- with one, the other end's gap is the source's own and is
            # reported as unattributed rather than swept into the boundary's
            # column.
            + (
                " -- this link is alone because the lasso cut "
                + (
                    "its junction(s) away"
                    if any(entry["junction_way_ids"] for entry in ends)
                    else "the road beyond it away (the source declares no other way at the "
                    "cut node(s))"
                )
                + ", not because the road ends"
                if len(ends) >= 2
                else f" -- only ONE of this link's two ends was cut by the boundary (its "
                f"{ends[0]['end']}); the other end was never cut, so whatever isolates it "
                "there is not attributed to the boundary"
            )
        ),
    }
    return {
        "orphan_boundary_attribution": attribution,
        "provenance": dict(provenance),
    }


# ---------------------------------------------------------------------------
# Unit #48 -- ONE lasso for EVERY boundary finding a job has.
#
# The demo failure this closes is not subtle: a job with two boundary-caused
# findings offered two buttons, each proposing a widened region derived from
# the SAME original polygon, so taking the second discarded the first. The
# user's own words for the result were that repeating the extension never
# healed the orphan -- correct, and the reason is that the V4 finger and the
# orphan's fingers open onto different edges of the ring entirely.
#
# The composite below derives every finger against ONE ring and splices them
# together. Per-finding proposals are untouched and stay on their own cards:
# this is an ADDITIONAL "all at once" option, not a replacement, and when it
# refuses (two mouths colliding, a corridor whose finger cannot be drawn, the
# area cap) the per-finding proposals are exactly where they were.
# ---------------------------------------------------------------------------

#: Where a composite extension's facts may ever come from, recorded in every
#: derivation's provenance the way :data:`_V4_EXTENSION_SOURCE_FIELD` is for
#: the single-finger one.
_BOUNDARY_EXTENSION_ALL_SOURCE_FIELD = (
    "boundary_attribution + orphan_boundary_attribution + "
    "metadata.boundary.requested_polygon_wgs84 + metadata.boundary.way_trims + way/nd"
)

#: The composite's own margin sentence. Same 20% overshoot and the same reason
#: :data:`_V4_EXTENSION_MARGIN_BASIS` gives; what it does NOT do is call every
#: finger's own number a "deficit", because half of them are not one -- a
#: severed orphan's finger is sized by the metres the trim ledger says the
#: boundary CUT, and reusing the runway word for that would be the payload
#: quietly renaming a measurement.
_BOUNDARY_EXTENSION_ALL_MARGIN_BASIS = (
    "each finger reaches its own measured shortage (basis_kind names which: a lane-change "
    "runway deficit, or the metres the boundary cut off a severed way) + 20% -- a round "
    "overshoot, named as round, because the re-extract cuts its own boundary node on the new "
    "ring and the new draft re-decomposes the way"
)

#: What a patch's own length was measured FROM, carried on every patch so a
#: reader is never left to assume that "the number the finger is sized by"
#: means the same thing on a runway shortage and on a severed orphan.
BASIS_RUNWAY_DEFICIT = "lane_change_runway_deficit"
BASIS_BOUNDARY_CUT = "boundary_cut_metres"


def _boundary_patch_specs_for_finding(
    finding: Mapping[str, Any],
    *,
    targets: Mapping[str, Any],
    draft: "DraftNetwork",
) -> list[dict[str, Any]]:
    """Every finger ONE finding earns, as specs the shared derivation can
    draw -- or an empty list when it earns none.

    Never re-litigates an attribution: it reads the ones
    :func:`derive_fix_targets` already derived (unit #39's for a runway
    shortage, unit #48's for an orphan) and turns each boundary-attributed end
    into ``{way_id, boundary_node, trim, basis_m, basis_kind, corridor_link,
    label, finding}``. A V4 shortage is ONE end by construction (the corridor
    has one upstream terminus); an orphan is one per SEVERED END, which is why
    the composite exists at all -- the motivating link was cut at both.
    """
    code = str(finding.get("code") or "")
    subject = str(finding.get("subject") or "")
    identity = {"code": code, "subject": subject}
    specs: list[dict[str, Any]] = []

    if code == V4_RUNWAY_FINDING_CODE:
        attribution = targets.get("boundary_attribution")
        if not isinstance(attribution, Mapping):
            return []
        if str(attribution.get("status") or "") != "boundary_attributed":
            return []
        try:
            deficit_m = float(attribution["deficit_m"])
            way_id = int(attribution["way_id"])
            boundary_node = int(attribution["boundary_node"])
        except (KeyError, TypeError, ValueError):
            return []
        if deficit_m <= 0:
            return []
        corridor_links = [
            link
            for link in draft.links
            if int(link.from_node_osm_id) == boundary_node and int(link.osm_way_id) == way_id
        ]
        if not corridor_links:
            return []
        trim = attribution.get("way_trim")
        specs.append(
            {
                "way_id": way_id,
                "boundary_node": boundary_node,
                "trim": dict(trim) if isinstance(trim, Mapping) else None,
                "trim_end": str(trim.get("end") or "") if isinstance(trim, Mapping) else "",
                "basis_m": round(deficit_m, 2),
                "basis_kind": BASIS_RUNWAY_DEFICIT,
                "corridor_link": min(corridor_links, key=lambda link: int(link.id)),
                "label": f"way {way_id} at node {boundary_node} ({code})",
                "finding": identity,
            }
        )
        return specs

    if code == V3_ORPHAN_FINDING_CODE:
        attribution = targets.get("orphan_boundary_attribution")
        if not isinstance(attribution, Mapping):
            return []
        if str(attribution.get("status") or "") != "boundary_attributed":
            return []
        try:
            way_id = int(attribution["way_id"])
            link = draft.link_by_id(int(str(attribution["link_id"])))
        except (KeyError, TypeError, ValueError):
            return []
        if link is None:
            return []
        for entry in attribution.get("severed_ends") or []:
            if not isinstance(entry, Mapping):
                continue
            try:
                metres_cut = float(entry["metres_cut"])
                boundary_node = int(entry["boundary_node"])
            except (KeyError, TypeError, ValueError):
                continue
            if metres_cut <= 0:
                # A tail cut whose overhang measured zero has no length to ask
                # for: the way's outermost node sat ON the ring. Nothing to
                # widen toward, and inventing a length would be the whole
                # doctrine broken for a rounding artefact.
                continue
            trim = entry.get("way_trim")
            specs.append(
                {
                    "way_id": way_id,
                    "boundary_node": boundary_node,
                    "trim": dict(trim) if isinstance(trim, Mapping) else None,
                    "trim_end": str(entry.get("end") or ""),
                    "basis_m": round(metres_cut, 2),
                    "basis_kind": BASIS_BOUNDARY_CUT,
                    "corridor_link": link,
                    "label": (
                        f"way {way_id}'s {entry.get('end')} at node {boundary_node} ({code})"
                    ),
                    "finding": identity,
                }
            )
    return specs


def derive_boundary_extension_for_findings(
    entries: "list[tuple[Mapping[str, Any], Mapping[str, Any]]]",
    *,
    draft: "DraftNetwork | None",
    job_id: str | None,
) -> dict[str, Any]:
    """Unit #48: ONE widened lasso covering EVERY boundary-attributed finding
    it is handed -- the user's own polygon with one finger per severed corridor
    spliced into the edges those corridors cross.

    ``entries`` are ``(finding, targets)`` pairs, ``targets`` being exactly
    what :func:`derive_fix_targets` produced for that finding, so this function
    consumes measurements rather than repeating them. Handed ONE entry it is
    that finding's own proposal (which is how the orphan card gets one, since
    an orphan can need two fingers); handed the whole verdict it is the "treat
    every boundary finding at once" option.

    Returns ``{"reason_code", "detail"}`` or::

        {"extended_polygon_wgs84": [[lat, lon], ...],
         "extended_area_km2": float, "added_area_km2": float,
         "corridor_patches": [{...}], "covers": [{...}], "provenance": {...}}

    **The named merge.** Two findings whose attributions land on the SAME
    corridor end (same way, same synthesized boundary node, same trimmed end)
    are ONE finger, not two: they are literally the same road being asked for
    twice. The longer request wins, both findings are recorded in that patch's
    ``covers``, and ``provenance['merged_patches']`` counts it. This is the
    only case where two specs become one shape, and it is safe precisely
    because the shape was never in dispute.

    **The named refusals.** ``no_boundary_findings`` (nothing handed here
    earned a finger), ``no_draft_census`` / ``requested_region_not_a_polygon``
    / ``source_artifact_missing`` (the shared preconditions #40 already names),
    ``corridor_patch_undrawable`` (a covered corridor's own finger could not be
    derived -- the composite refuses rather than quietly covering fewer
    findings than its own label claims, and that corridor's per-finding
    proposal is unaffected), plus every reason
    :func:`_splice_patches_into_ring` can return, above all
    ``patch_mouths_collide_on_edge``.
    """
    from services.genesis.project import LocalTangentPlane  # noqa: PLC0415

    def _nothing(reason_code: str, detail: str) -> dict[str, Any]:
        return {"reason_code": reason_code, "detail": detail}

    if draft is None:
        return _nothing(
            "no_draft_census",
            "this job's *.genesis.json census is absent, unreadable, or does not validate",
        )
    metadata = draft.metadata or {}
    boundary_meta = metadata.get("boundary")
    boundary_meta = boundary_meta if isinstance(boundary_meta, Mapping) else {}
    ring_wgs84 = _draft_requested_ring(boundary_meta)
    if ring_wgs84 is None:
        return _nothing(
            "requested_region_not_a_polygon",
            "the census records no requested POLYGON for this job (a rectangle region, or a "
            "census older than the field). The treatment hands a lasso to the lasso page, "
            "and rendering a rectangle as a lasso would show the user corners they never drew",
        )

    specs: list[dict[str, Any]] = []
    for finding, targets in entries:
        specs.extend(
            _boundary_patch_specs_for_finding(finding, targets=targets, draft=draft)
        )
    if not specs:
        return _nothing(
            "no_boundary_findings",
            "none of the findings handed here carries a boundary attribution that earns a "
            "widened region -- an interior shortage is the road's, and an orphan the source "
            "itself leaves unconnected is the world's",
        )

    # ---- the named merge: the same corridor end asked for twice ------------
    merged: dict[tuple[int, int, str], dict[str, Any]] = {}
    merged_count = 0
    for spec in specs:
        key = (int(spec["way_id"]), int(spec["boundary_node"]), str(spec["trim_end"]))
        existing = merged.get(key)
        if existing is None:
            spec = dict(spec)
            spec["covers"] = [dict(spec["finding"])]
            merged[key] = spec
            continue
        merged_count += 1
        existing["covers"].append(dict(spec["finding"]))
        if float(spec["basis_m"]) > float(existing["basis_m"]):
            existing["basis_m"] = spec["basis_m"]
            existing["basis_kind"] = spec["basis_kind"]
            existing["label"] = spec["label"]
    ordered = [merged[key] for key in sorted(merged)]

    index = _source_osm_index(job_id) if job_id else None
    if index is None:
        return _nothing(
            "source_artifact_missing",
            "the job's source .osm is absent or unreadable, so the roads' own directions "
            "beyond the boundary cannot be measured and would have to be guessed",
        )

    # The region page's own convention: one plane about the requested polygon's
    # BOUNDING-BOX centre, shared by every finger, so two patches can never be
    # measured in two different sets of metres.
    lats = [lat for lat, _lon in ring_wgs84]
    lons = [lon for _lat, lon in ring_wgs84]
    plane = LocalTangentPlane(
        (min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0
    )
    ring_xy = [plane.forward(lat, lon) for lat, lon in ring_wgs84]

    patches: list[dict[str, Any]] = []
    for spec in ordered:
        way_id = int(spec["way_id"])
        if way_id not in index["ways"]:
            return _nothing(
                "corridor_patch_undrawable",
                f"{spec['label']}: the source holds no drivable way with id {way_id}, so "
                "this corridor's finger could not be drawn and the composite refuses rather "
                "than covering fewer findings than it claims",
            )
        basis_m = float(spec["basis_m"])
        margin_m = round(basis_m * _V4_EXTENSION_MARGIN_FRACTION, 2)
        needed_m = round(basis_m + margin_m, 2)
        patch = _boundary_patch_from_corridor(
            index=index,
            plane=plane,
            ring_wgs84=ring_wgs84,
            ring_xy=ring_xy,
            way_id=way_id,
            boundary_node=int(spec["boundary_node"]),
            trim=spec["trim"],
            corridor_link=spec["corridor_link"],
            needed_m=needed_m,
            margin_m=margin_m,
        )
        if "reason_code" in patch:
            return _nothing(
                "corridor_patch_undrawable",
                f"{spec['label']}: {patch['reason_code']} -- {patch['detail']}. The "
                "composite refuses rather than covering fewer findings than it claims; this "
                "finding's own proposal is unaffected",
            )
        patch = dict(patch)
        patch["label"] = spec["label"]
        patch["way_id"] = way_id
        patch["boundary_node"] = int(spec["boundary_node"])
        patch["basis_m"] = round(basis_m, 2)
        patch["basis_kind"] = str(spec["basis_kind"])
        patch["margin_m"] = margin_m
        patch["covers"] = [dict(entry) for entry in spec["covers"]]
        patches.append(patch)

    spliced = _splice_patches_into_ring(ring_wgs84=ring_wgs84, patches=patches)
    if "reason_code" in spliced:
        return dict(spliced)

    covers: list[dict[str, Any]] = []
    for finding, _targets in entries:
        identity = {
            "code": str(finding.get("code") or ""),
            "subject": str(finding.get("subject") or ""),
        }
        mine = [
            patch
            for patch in patches
            if any(entry == identity for entry in patch["covers"])
        ]
        if not mine:
            continue
        covers.append(
            {
                **identity,
                "patch_count": len(mine),
                "basis_kind": mine[0]["basis_kind"],
                "basis_m": round(sum(float(patch["basis_m"]) for patch in mine), 2),
                "length_m": round(sum(float(patch["length_m"]) for patch in mine), 2),
                "measured_source_m": round(
                    sum(float(patch["measured_source_m"]) for patch in mine), 2
                ),
                "extrapolated_m": round(
                    sum(float(patch["extrapolated_m"]) for patch in mine), 2
                ),
            }
        )

    return {
        **spliced,
        "corridor_patches": [
            {
                "crossing_edge_index": patch["edge_index"],
                "crossing_point_wgs84": list(plane.inverse(*patch["crossing_xy"])),
                "centreline_wgs84": [
                    list(plane.inverse(x, y)) for x, y in patch["centreline_xy"]
                ],
                "length_m": patch["length_m"],
                "measured_source_m": patch["measured_source_m"],
                "extrapolated_m": patch["extrapolated_m"],
                "half_width_m": patch["half_width_m"],
                "roadway_width_m": patch["roadway_width_m"],
                "continuation_way_ids": patch["continuation_way_ids"],
                "vertices_added": patch["vertices_added"],
                "way_id": patch["way_id"],
                "boundary_node": patch["boundary_node"],
                "basis_m": patch["basis_m"],
                "basis_kind": patch["basis_kind"],
                "margin_m": patch["margin_m"],
                "covers": patch["covers"],
            }
            for patch in patches
        ],
        "covers": covers,
        "provenance": {
            "source_artifact": "genesis_json+source_osm",
            "source_field": _BOUNDARY_EXTENSION_ALL_SOURCE_FIELD,
            "parent_job": job_id,
            "patch_count": len(patches),
            "finding_count": len(covers),
            "merged_patches": merged_count,
            "basis": _BOUNDARY_EXTENSION_ALL_MARGIN_BASIS,
            "treatment": "new_generation_from_a_wider_extract",
        },
    }


def derive_fix_targets(
    code: str,
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None" = None,
    params: Mapping[str, Any] | None = None,
    job_id: str | None = None,
    attestation: Mapping[str, Any] | None = None,
    dead_end_confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``targets`` :func:`services.agent.genesis_fixes.propose_genesis_fix`
    is re-run with — built from what THIS service layer can honestly derive,
    never invented. Shared by the fixes-listing route (``params=None``:
    never a volume, but the MEASUREMENT evidence still flows, so a measured
    entrance shows its structural refusal — fix round item 3) and
    :func:`create_genesis_fix_job`'s own re-derivation (``params`` = the
    approver's submitted payload: the judgment-call VALUE a human is
    accountable for feeds back in as evidence, while the STRUCTURAL facts
    below are always re-verified for real).

    * **V9** (``link_length_m``) — objective network geometry read straight
      off the job's own stored census (``draft.link_by_id(...).length_m``).
      Never ask-first: a link's length is not a judgment call.
    * **V6** (``node``) — real observed-count evidence for the finding's own
      link, via :func:`derive_v6_node_evidence` (see its docstring for
      exactly what that join can and cannot see today). Derived
      UNCONDITIONALLY, independent of ``params`` — the measurement doctrine
      must be checkable even when nobody has proposed a volume yet.
    * **V6** (``proposed_volume``) — the ask-first field. Threaded from
      ``params`` ONLY (never derived here): exactly the value the module
      docstring by ``genesis_fixes._build_v6_entrance_volume`` names as the
      APPROVER's alone to supply.
    * **V7** (``corrected_points_xy`` + ``corrected_points_provenance``) —
      unit #34: derived by :func:`derive_v7_corrected_polyline` from the
      DRAFT's OWN declared polyline for the finding's link (``job_id`` names
      the job whose census and emitted artifact are read), or nothing.
      ``params['points_xy']`` is deliberately IGNORED here — an approver's
      own coordinates are exactly the fabrication the V7 doctrine refuses
      (``_build_v7_geometry_fix``'s "a polyline this module did not invent"),
      and :func:`create_genesis_fix_job` additionally refuses submitted
      points that differ from this derivation
      (``genesis_fix_points_not_census_derived``).
    * **V3** (``source_testimony`` + ``source_testimony_provenance``) — unit
      #36: derived by :func:`derive_v3_source_testimony` from the job's own
      stored source ``.osm``. For six of the seven V3 codes this stays
      EVIDENCE ONLY, exactly as unit #36 shipped it: those entries remain
      ``NOT_AUTOMATABLE`` under ``CONNECTIVITY_IS_NOT_INVENTED`` whichever way
      the testimony falls, and the derivation only lets the refusal's
      rationale state, measured, whether the source itself shares the gap or
      declares the connection the pipeline lost.
    * **V3** (``connector_params`` + ``connector_derivation`` +
      ``human_attestation`` + ``attestation_candidates``) — unit #35, for
      :data:`V3_TREATABLE_FINDING_CODE` alone. The testimony above, or the
      approver's own ``attestation``, is turned into a real
      ``add_connector`` payload by :func:`derive_v3_connector_plan` — the
      CONNECTION named by a source, the geometry constructed from two link
      ends the census already declares. ``attestation`` is threaded from the
      caller ONLY (never derived here — it is a human's claim, the same way
      V6's ``proposed_volume`` is a human's number), and when no plan could be
      derived the picker a human would attest FROM
      (:func:`derive_v3_attestation_candidates`) rides along so the refusal
      can carry the ask-first question instead of being a dead end.
    * **V3** (``dead_end_confirmation`` + ``dead_end_derivation``) — unit #42,
      for :data:`V3_TREATABLE_FINDING_CODE` alone and the OTHER answer a world
      gap can have: the road really ends. Derived by
      :func:`derive_v3_dead_end_confirmation` — an invitation naming exactly
      which way an exclusion would remove and what the source says would come
      loose with it, or a NAMED refusal (most importantly
      ``source_testifies_the_connection``: a human confirmation may not erase
      road the source declares). ``dead_end_confirmation`` is the caller's own
      threaded claim, never derived here, exactly like ``attestation`` above,
      and neither key changes the tier — the treatment is a NEW re-derivation
      without the confirmed way (``create_dead_end_exclusion_job``), never an
      edit to this artifact.
    * **V4** (``boundary_attribution`` + ``boundary_attribution_provenance``)
      — unit #39, for :data:`V4_RUNWAY_FINDING_CODE` alone: derived by
      :func:`derive_v4_boundary_attribution` from the judge's own stored
      numbers, the draft census's boundary markers and the job's stored
      source ``.osm``. EVIDENCE ONLY, like V3's testimony: the refusal's
      rationale states, measured, whether the runway shortage is the
      requested boundary's or the road's — the tier never changes with the
      answer.
    * **V4** (``boundary_extension`` / ``boundary_extension_refusal``) — unit
      #40, derived from the attribution above by
      :func:`derive_v4_boundary_extension`: the user's own requested lasso
      with a finger spliced in at the edge the corridor crosses, long enough
      to cover the measured deficit plus a stated margin. Still not a tier
      change and still not an edit to this artifact — the treatment is a NEW
      GENERATION from a wider extract (never unit #38's same-source
      regeneration, whose bytes are clipped at this very boundary), and the
      proposal's only destination is the region page's editable lasso, whose
      own extract button is the ask-first gate.
    """
    from services.agent.fixes import node_evidence_from_real_counts  # noqa: PLC0415
    from services.agent.genesis_fixes import expected_fix_target  # noqa: PLC0415

    targets: dict[str, Any] = {}
    context = dict(finding.get("context") or {})

    if code == "v9_stop_line_without_queue_counter" and draft is not None:
        link_id_text = expected_fix_target(code, finding)
        link = None
        if link_id_text:
            try:
                link = draft.link_by_id(int(link_id_text))
            except (ValueError, TypeError):
                link = None
        if link is not None:
            targets["link_length_m"] = link.length_m

    if code in ("v6_input_without_volume", "v6_input_volume_is_zero"):
        link_id = context.get("link")
        if link_id:
            raw_node = derive_v6_node_evidence(str(link_id))
            if raw_node is not None:
                targets["node"] = node_evidence_from_real_counts(raw_node)
        if params is not None and params.get("volume") is not None:
            targets["proposed_volume"] = params.get("volume")

    if code in V7_GEOMETRY_FINDING_CODES:
        derivation = derive_v7_corrected_polyline(code, finding, draft=draft, job_id=job_id)
        if derivation["corrected_points_xy"] is not None:
            targets["corrected_points_xy"] = derivation["corrected_points_xy"]
        targets["corrected_points_provenance"] = derivation["provenance"]

    if code in V3_CONNECTIVITY_FINDING_CODES:
        testimony = derive_v3_source_testimony(code, finding, draft=draft, job_id=job_id)
        if testimony["source_testimony"] is not None:
            targets["source_testimony"] = testimony["source_testimony"]
        targets["source_testimony_provenance"] = testimony["provenance"]

    if code == V3_ORPHAN_FINDING_CODE:
        # Unit #48: the OTHER thing an orphan can be -- a link the requested
        # lasso severed at one or both ends. Derived UNCONDITIONALLY (never
        # gated on the testimony above failing) for the reason unit #36's own
        # measurement makes plain: on the motivating job the testimony read
        # `node_not_in_source`, which is TRUE and says nothing, because the node
        # it asked about is one this generator synthesized on the ring. The
        # ledger knew. Evidence only, exactly like #39's: the tier stays
        # NOT_AUTOMATABLE under CONNECTIVITY_IS_NOT_INVENTED whichever way it
        # lands, and an orphan with no trim record is offered NO region.
        orphan = derive_v3_orphan_boundary_attribution(
            code, finding, draft=draft, job_id=job_id
        )
        if orphan["orphan_boundary_attribution"] is not None:
            targets["orphan_boundary_attribution"] = orphan["orphan_boundary_attribution"]
        targets["orphan_boundary_attribution_provenance"] = orphan["provenance"]
        # ...and the TREATMENT that attribution earns: one finger per severed
        # end, spliced into the user's own lasso. Derived through the SAME
        # composite the job-level "all at once" option uses, restricted to this
        # finding -- an orphan cut at both ends needs two fingers on one ring,
        # which is exactly the shape #40's single-patch payload cannot carry.
        if orphan["orphan_boundary_attribution"] is not None:
            extension = derive_boundary_extension_for_findings(
                [(finding, targets)], draft=draft, job_id=job_id
            )
            if "reason_code" in extension:
                targets["boundary_extension_patches_refusal"] = extension
            else:
                targets["boundary_extension_patches"] = extension

    if code == V4_RUNWAY_FINDING_CODE:
        attribution = derive_v4_boundary_attribution(code, finding, draft=draft, job_id=job_id)
        if attribution["boundary_attribution"] is not None:
            targets["boundary_attribution"] = attribution["boundary_attribution"]
        targets["boundary_attribution_provenance"] = attribution["provenance"]
        # Unit #40: the TREATMENT the attribution above earns -- a wider lasso,
        # derived only for a shortage the boundary actually caused. A refusal
        # is carried under its own key so the rationale can NAME it instead of
        # the card going quiet; neither key changes the tier.
        extension = derive_v4_boundary_extension(
            finding, targets.get("boundary_attribution"), draft=draft, job_id=job_id
        )
        if "reason_code" in extension:
            targets["boundary_extension_refusal"] = extension
        else:
            targets["boundary_extension"] = extension

    if code == V3_TREATABLE_FINDING_CODE:
        plan = derive_v3_connector_plan(
            finding,
            draft=draft,
            testimony=targets.get("source_testimony"),
            attestation=attestation,
        )
        if plan["connector_params"] is not None:
            targets["connector_params"] = plan["connector_params"]
        targets["connector_derivation"] = plan["provenance"]
        if attestation is not None:
            targets["human_attestation"] = dict(attestation)
        if plan["connector_params"] is None:
            candidates = derive_v3_attestation_candidates(finding, draft=draft)
            if candidates:
                targets["attestation_candidates"] = candidates
        # Unit #42: the OTHER thing a world gap can be -- a road that really
        # ends. Derived UNCONDITIONALLY (never gated on the connector plan
        # failing) for one reason: the source-testimony case must reach the
        # refusal clause too, so a reader who wants to erase a gap the SOURCE
        # declares is told why they may not, instead of finding no control and
        # guessing. ``confirmation`` is threaded from the caller ONLY, exactly
        # as ``attestation`` above is -- it is a human's claim, never derived.
        dead_end = derive_v3_dead_end_confirmation(
            finding,
            draft=draft,
            job_id=job_id,
            testimony=targets.get("source_testimony"),
            testimony_provenance=targets.get("source_testimony_provenance"),
            confirmation=dead_end_confirmation,
        )
        if dead_end["dead_end_confirmation"] is not None:
            targets["dead_end_confirmation"] = dead_end["dead_end_confirmation"]
        targets["dead_end_derivation"] = dead_end["provenance"]

    return targets


def create_genesis_fix_job(
    parent_job_id: str,
    *,
    finding_code: str,
    subject: str | None = None,
    verb: str,
    params: dict[str, Any],
    approved_by: str | None,
    approval_rationale: str | None,
    attestation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Register a fix job WITHOUT starting it — the clone step.

    Validates, in order, before a job id is ever minted (so any refusal
    below leaves no job directory on disk):

    1. the parent job exists in THIS workspace (:func:`get_job` raises
       :class:`GenesisJobNotFoundError` the same way it does for any unknown
       id — a fix job's parent is not a special case here);
    2. the parent's verdict is :data:`STATUS_REFUSED`. A fix job only makes
       sense against a refusal — the parent's artifact and gate result are
       the diagnosis this fix answers, and a parent that published or went
       provisional has no such diagnosis to answer. Refused with the named
       code ``genesis_fix_parent_not_refused``;
    3. ``verb``/``params`` still resolve against the LIVE tool registry, via
       ``services.agent.fixes.resolve_verb``/``validate_params`` — the exact
       functions :mod:`services.agent.genesis_fixes` used to PROPOSE this fix
       in the first place, called again HERE because approval can arrive
       long after proposal (a human reviewing a refusal queue), and a verb
       renamed or re-tiered in ``tools_edit.py`` in the meantime must refuse
       cleanly rather than crash the runner on a verb that no longer exists.
       Refused with the named code ``genesis_fix_verb_no_longer_registered``;
    4. **``finding_code`` -- plus ``subject``, when given -- actually
       resolves to ONE finding in the PARENT's own stored verdict**
       (:func:`_finding_in_stored_gate`) — a caller must not be able to mint
       a fix job for a diagnosis the parent's gate never made (e.g. a V6
       finding_code against a V3-only parent). Refused with the named code
       ``genesis_fix_finding_not_in_parent_verdict``, naming the subject
       looked for when one was given. And when ``subject`` is OMITTED while
       the code appears on more than one subject in that verdict, the lookup
       itself refuses with the named code
       ``genesis_fix_finding_ambiguous_without_subject`` listing every
       candidate subject (unit #30) — silently resolving duplicates to the
       first match was B5-2's own recorded limit, not a behavior to keep;
    5. **the submitted ``params`` ADDRESS the exact element that finding is
       about** — checked against the finding's OWN subject/context via
       ``services.agent.genesis_fixes.VERB_ADDRESSING_FIELD`` /
       ``expected_fix_target``, so params approved for one object (vehicle
       input 1) cannot be applied under a finding_code that happens to also
       match a DIFFERENT object (vehicle input 2). Refused with the named
       code ``genesis_fix_params_target_mismatch``;
    6. **the fix registry, RE-RUN against REAL server-side evidence, still
       reaches a matching proposal** — :func:`derive_fix_targets` builds the
       ``census``/``targets`` this service layer can honestly derive (the
       parent's own stored geometry, and this workspace's ingested
       observation sets for V6's measured-entrance doctrine — unit D4),
       ``services.agent.genesis_fixes.propose_genesis_fix`` is called with
       them, and the result must be a ``PROPOSAL`` naming the SAME verb. A
       structural refusal here — most importantly a MEASURED entrance
       (``INPUT_VOLUME_IS_OBSERVED_DATA``) — means the approval this call
       carries no longer holds against reality, however innocuous the
       request looked. Refused with the named code
       ``genesis_fix_measurement_gate_not_satisfied``, carrying the
       registry's OWN reason code and rationale.
    7. **derived coordinates cannot be swapped for the approver's own** (unit
       #34) — when step 6's re-derivation produced ``corrected_points_xy``
       from the parent's OWN census (:func:`derive_v7_corrected_polyline`),
       the submitted ``params['points_xy']`` must equal that derivation.
       The runner applies the SUBMITTED params, so without this check an
       approval could ride a valid derivation while carrying coordinates no
       census ever declared. Refused with the named code
       ``genesis_fix_points_not_census_derived``.
    8. **derived CONNECTOR params cannot be swapped for the approver's own**
       (unit #35) — the exact same guard, applied to
       :func:`derive_v3_connector_plan`'s output: when step 6's re-derivation
       produced ``connector_params``, the submitted ``params`` must equal them
       field for field (:func:`normalized_connector_params`). Refused with the
       named code ``genesis_fix_connector_not_derived``.

    ``attestation`` (unit #35) is the ONE judgment call this door accepts for a
    V3 treatment: ``{to_link, basis}`` — a human's claim that the real road
    continues, and where to. It is stamped HERE with the approver's own name
    and the server's clock (``attested_by``/``attested_at``: the client has no
    field for either, the same discipline ``approved_by`` follows) and fed back
    into the re-derivation as evidence, so the registry — not this function —
    decides what an attestation is worth. An attestation missing its target or
    its basis is refused by name (``genesis_fix_attestation_incomplete``)
    rather than completed with a default.

    **Why THIS is the authoritative gate, not merely a convenience.**
    ``routers/genesis.py``'s ``apply_genesis_fix`` runs an equivalent but
    CHEAPER static check (finding_code resolves + names an AUTOMATABLE
    mapping whose verb matches) before ever reaching this function — a
    fast-fail for the common case. That router-side check alone is
    defeatable (any caller that reaches this function directly, or a future
    router bug) and does not re-verify anything against the PARENT's actual
    verdict or REAL evidence; checks 4-6 above are what actually make an
    exploit die, at the one layer every caller — HTTP or otherwise — must
    pass through. Keep both: the redundancy is deliberate, not an oversight.

    Only after ALL of the above: a new job id is minted, its own work
    directory created, and the parent's CURRENT ``.inpx`` artifact bytes
    (:func:`artifact_path`, which itself refuses if the parent's artifact is
    somehow gone) are copied into it under the SAME base name the parent
    used — never the parent's own file, never opened for writing, only ever
    read from. The clone's sha256 is computed from the CLONED bytes
    (AFTER ``shutil.copyfile`` returns, not from a separate pre-copy read of
    the parent's file — a TOCTOU fix: the parent's artifact is never
    modified in place, but hashing what was ACTUALLY cloned rather than a
    second, independent read of the source is the only way this value can
    ever disagree with itself) and recorded as ``derived_from.parent_sha256``
    — lineage anchored to the bytes that were actually cloned, not to the
    parent job's id or its own (possibly stale) recorded hash.

    ``derived_from`` is the whole point of a sibling job: ``{job_id,
    finding_code, subject, verb, params, approved_by, approval_rationale,
    approved_at, parent_sha256}`` — plus ``census_derivation`` when and only
    when the applied coordinates were derived from the parent's own census
    (unit #34; key-ABSENT on every other fix job) — present ONLY on a fix
    job's record — an
    ordinary generation's record carries no such key at all (key-ABSENT, not
    ``null``), the same "presence is itself the fact" discipline
    ``requested_region`` already established in this module, and the one
    ``tests/test_genesis_fix_jobs.py`` pins across the listing route's own
    JSON round trip. ``subject`` (unit #30) is always the RESOLVED finding's
    own subject — even when the caller omitted it on a single-match code —
    so the lineage names exactly which object the fix answered; records
    written before this field existed simply lack the key and keep reading
    fine.

    Raises:
        GenesisJobNotFoundError: no such parent job in this workspace.
        GenesisRefused: the parent is not refused, the approved verb/params
            have gone stale, the finding does not appear in the parent's own
            verdict, the finding_code is ambiguous without a subject, the
            params address a different object than the finding names, or the
            registry's real-evidence re-derivation does not reach a matching
            proposal. None of these create a job directory.
    """
    workspace_id = workspaces.current_workspace_id()
    parent = get_job(parent_job_id)
    if parent.get("status") != STATUS_REFUSED:
        raise GenesisRefused(
            "genesis_fix_parent_not_refused",
            [
                f"Job {parent_job_id} is {parent.get('status')!r}, not {STATUS_REFUSED!r}. "
                "A fix job only makes sense against a refused generation -- its artifact and "
                "gate result are the diagnosis this fix is meant to answer."
            ],
        )

    # Function-local: this is the ONE call site in this module that needs the
    # agent's fix-mapping registries, and both ``services.agent.fixes`` and
    # ``services.agent.genesis_fixes`` are themselves COM-free (their own
    # module docstrings: "Nothing here ... touches COM" / "No COM, no I/O,
    # no Vissim") -- this is about keeping this module's import surface
    # minimal, not about the COM discipline the runner below is actually
    # subject to.
    from services.agent import fixes as agent_fixes  # noqa: PLC0415
    from services.agent import genesis_fixes as agent_genesis_fixes  # noqa: PLC0415

    resolution = agent_fixes.resolve_verb(verb)
    if not resolution["resolved"]:
        raise GenesisRefused(
            "genesis_fix_verb_no_longer_registered",
            [
                f"{verb!r} no longer resolves in the live tool registry "
                f"({resolution['why']}). This approved proposal has gone stale since it was "
                "made -- propose a fresh fix rather than approving this one."
            ],
        )
    params_ok, why = agent_fixes.validate_params(verb, params)
    if not params_ok:
        raise GenesisRefused(
            "genesis_fix_verb_no_longer_registered",
            [
                f"{verb!r}'s approved params no longer validate against its live schema "
                f"({why}). This approved proposal has gone stale since it was made -- propose "
                "a fresh fix rather than approving this one."
            ],
        )

    # --- unit #35: an attestation is a claim somebody is accountable for, so
    # it is stamped with WHO and WHEN here (never taken from the client) and
    # refused outright when it is missing the two things only a human can
    # supply. Completing an incomplete attestation with a default would be
    # this system attesting on a person's behalf.
    stamped_attestation: dict[str, Any] | None = None
    if attestation is not None:
        to_link_text = str(attestation.get("to_link") or "").strip()
        basis_text = str(attestation.get("basis") or "").strip()
        attested_by = str(approved_by or "").strip()
        missing = [
            name
            for name, value in (("to_link", to_link_text), ("basis", basis_text))
            if not value
        ]
        if not attested_by:
            missing.append("attested_by")
        if missing:
            raise GenesisRefused(
                "genesis_fix_attestation_incomplete",
                [
                    "An attestation must name the target link, state its basis, and be "
                    f"attributable to a signed-in person. Missing: {', '.join(missing)}. "
                    "Nothing here fills those in on a human's behalf."
                ],
            )
        stamped_attestation = {
            "to_link": to_link_text,
            "basis": basis_text,
            "attested_by": attested_by,
            "attested_at": _now_iso(),
        }

    # --- exploit #3's fix: the finding must actually be IN the parent's own
    # verdict, not merely a code this catalogue recognises in the abstract.
    raw_parent_gate = json.loads(
        (job_dir(parent_job_id) / GATE_JSON_FILENAME).read_text(encoding="utf-8")
    )
    finding = _finding_in_stored_gate(raw_parent_gate, finding_code, subject=subject)
    if finding is None:
        looked_for = (
            f"Finding code {finding_code!r} with subject {subject!r}"
            if subject is not None
            else f"Finding code {finding_code!r}"
        )
        raise GenesisRefused(
            "genesis_fix_finding_not_in_parent_verdict",
            [
                f"{looked_for} does not appear anywhere in job "
                f"{parent_job_id}'s stored gate verdict -- there is no diagnosis for this fix "
                "to answer."
            ],
        )

    # --- exploit #2's fix: params must address the SAME element the finding
    # is about, not merely be well-formed for the verb in the abstract.
    addressing_field = agent_genesis_fixes.VERB_ADDRESSING_FIELD.get(verb)
    expected_target = agent_genesis_fixes.expected_fix_target(finding_code, finding)
    if addressing_field and expected_target is not None:
        submitted_target = params.get(addressing_field)
        if submitted_target is None or str(submitted_target) != expected_target:
            raise GenesisRefused(
                "genesis_fix_params_target_mismatch",
                [
                    f"Finding {finding_code!r} (subject {finding['subject']!r}) names "
                    f"{expected_target!r} as its {addressing_field!r}, but the submitted "
                    f"params name {submitted_target!r} instead. A fix must address exactly "
                    "the element the finding diagnosed, never a different one under the same "
                    "verb -- this is checked even when the mismatched object also exists and "
                    "is otherwise a valid target."
                ],
            )

    # --- exploit #1's fix: re-run the registry against REAL evidence (this
    # job's own stored census, and this workspace's ingested observation
    # sets for V6 -- see derive_fix_targets/derive_v6_node_evidence for
    # exactly what is derived and what is never invented) and require it to
    # reach the SAME proposal the caller is claiming to apply.
    draft = job_draft_or_none(parent_job_id)
    census = draft.metadata if draft is not None else None
    targets = derive_fix_targets(
        finding_code,
        finding,
        draft=draft,
        params=params,
        job_id=parent_job_id,
        attestation=stamped_attestation,
    )
    proposal = agent_genesis_fixes.propose_genesis_fix(
        finding_code, finding=finding, census=census, targets=targets
    )
    if proposal.outcome != agent_fixes.PROPOSAL or proposal.verb != verb:
        raise GenesisRefused(
            "genesis_fix_measurement_gate_not_satisfied",
            [
                f"The fix registry re-derived finding {finding_code!r}'s disposition from REAL "
                f"server-side evidence and did not reach a proposal matching verb {verb!r} "
                f"(outcome={proposal.outcome!r}, reason_code={proposal.reason_code!r}). "
                + (proposal.rationale_en or "")
            ],
        )

    # --- unit #34's own guard: when the proposal's coordinates were DERIVED
    # from the draft's own record, the approved params must carry exactly
    # those coordinates. The runner applies derived_from.params, so without
    # this an approver could ride a valid derivation while submitting
    # coordinates nobody's census ever declared -- fabrication wearing an
    # approval. (The verb/outcome check above cannot catch it: the proposal
    # matches, only the payload lies.)
    derived_points = targets.get("corrected_points_xy")
    if derived_points is not None:
        submitted_points = _rounded_polyline(params.get("points_xy"), 6)
        if submitted_points != _rounded_polyline(derived_points, 6):
            raise GenesisRefused(
                "genesis_fix_points_not_census_derived",
                [
                    f"The submitted points_xy differ from the correction the draft's own census "
                    f"declares for finding {finding_code!r} (subject {finding['subject']!r}). "
                    "A V7 geometry fix's coordinates come from the draft's own pre-corruption "
                    "record, never from the approver -- approve the derived correction or "
                    "reject it, but do not replace it."
                ],
            )

    # --- unit #35's mirror of the same guard, for the V3 connector treatment.
    # The CONNECTION may come from a source or from an attesting human; the
    # PARAMS never come from the approver -- they are constructed here out of
    # two link ends the census declares, and the runner applies exactly what
    # is stored, so a payload that differs from the derivation is refused.
    derived_connector = targets.get("connector_params")
    if derived_connector is not None:
        submitted_connector = normalized_connector_params(params)
        if submitted_connector != normalized_connector_params(derived_connector):
            raise GenesisRefused(
                "genesis_fix_connector_not_derived",
                [
                    f"The submitted add_connector params differ from the connector this "
                    f"service re-derived for finding {finding_code!r} (subject "
                    f"{finding['subject']!r}) from the named connection and the two link ends "
                    f"the census declares: {normalized_connector_params(derived_connector)!r}. "
                    "A connector's geometry and lane mapping are constructed, never approved "
                    "in -- approve the derived connector or reject it, but do not replace it."
                ],
            )

    parent_artifact = artifact_path(parent_job_id, "inpx")

    job_id = uuid4().hex[:12]
    work_dir = job_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    name = str(parent.get("name") or "fix")
    clone_path = work_dir / f"{name}.inpx"
    # A plain copy, never a move and never opened for writing here -- the
    # parent's own artifact file is read once and never touched again by
    # anything in this module. ``run_genesis_fix_job`` opens its COM session
    # on THIS clone, in the NEW job's own work dir.
    shutil.copyfile(parent_artifact, clone_path)
    # TOCTOU fix: hash the CLONE's bytes, after the copy -- see the
    # docstring above. One read, reused for both the new job's own artifact
    # sha AND the lineage sha, since both name the exact same bytes.
    clone_sha256 = _sha256(clone_path)

    record = {
        "id": job_id,
        "workspace_id": workspace_id,
        "name": name,
        "status": STATUS_QUEUED,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "submitted_by": approved_by,
        "options": {},
        # A fix job re-uses the parent's OWN artifact bytes -- there is no
        # ``.osm`` of its own to re-parse, unlike an ordinary generation.
        "source_osm": None,
        "network": None,
        "artifact": {
            "inpx": clone_path.name,
            "compliance_md": None,
            # Named by convention (matches an ordinary job's own naming) but
            # not copied from the parent -- the parent's census describes ITS
            # network, and after the runner's edit this job's network is no
            # longer byte-identical to it. Absent on disk, ``gate_and_publish``
            # reads that as "no census" (Provenance UNKNOWN) exactly as it
            # does for any hand-uploaded network; see its own docstring.
            "genesis_json": f"{name}.genesis.json",
            "sha256": clone_sha256,
            "bytes": clone_path.stat().st_size,
        },
        "layers": None,
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
        "derived_from": {
            "job_id": parent_job_id,
            "finding_code": finding_code,
            # The RESOLVED finding's own subject, not the caller's raw input:
            # a code-only single-match create still records exactly which
            # object it landed on (unit #30 -- lineage precision).
            "subject": finding["subject"],
            "verb": verb,
            "params": dict(params),
            "approved_by": approved_by,
            "approval_rationale": approval_rationale,
            "approved_at": _now_iso(),
            "parent_sha256": clone_sha256,
            # Unit #34: when the applied coordinates were DERIVED from the
            # parent's own census, the lineage names the exact source field
            # and link id (key-ABSENT for every non-derived fix, the same
            # presence-is-the-fact discipline the rest of this record keeps).
            **(
                {"census_derivation": targets["corrected_points_provenance"]}
                if derived_points is not None and targets.get("corrected_points_provenance")
                else {}
            ),
            # Unit #35: the same presence-is-the-fact discipline for the V3
            # connector treatment -- the derivation that BUILT the applied
            # connector, and (only when a human made the claim) the
            # attestation verbatim, attribution included. Both key-ABSENT on
            # every other fix job.
            **(
                {"connector_derivation": targets["connector_derivation"]}
                if derived_connector is not None and targets.get("connector_derivation")
                else {}
            ),
            # An attestation is recorded under "attestation" ONLY when it is the
            # basis the connector was actually derived from. When the source's
            # own testimony resolved the target first, the human's submission
            # still happened -- erasing it would falsify the audit trail in the
            # other direction -- so it is kept under a key that names its
            # disposition, never beside applied params it did not produce.
            **(
                {"attestation": stamped_attestation}
                if stamped_attestation is not None
                and (targets.get("connector_derivation") or {}).get("basis") != "source_testimony"
                else {}
            ),
            **(
                {
                    "attestation_superseded": {
                        **stamped_attestation,
                        "superseded_by": "source_testimony",
                    }
                }
                if stamped_attestation is not None
                and (targets.get("connector_derivation") or {}).get("basis") == "source_testimony"
                else {}
            ),
        },
    }
    _upsert(record)
    return record


def start_genesis_fix_job(parent_job_id: str, **kwargs: Any) -> dict[str, Any]:
    """:func:`create_genesis_fix_job`, then run it on a daemon worker thread.

    Mirrors :func:`start_genesis_job`'s own split exactly, and for the same
    reason: creating the job record (and cloning the parent's artifact) is
    fast, synchronous, request-thread work; applying the verb is a live COM
    turn and must never run inside a request handler (CLAUDE.md's COM
    discipline). Tests that want a synchronous result call
    :func:`create_genesis_fix_job` + :func:`run_genesis_fix_job` directly,
    the same split :func:`_generate` uses for ordinary jobs in
    ``tests/test_genesis_jobs.py``.
    """
    record = create_genesis_fix_job(parent_job_id, **kwargs)
    threading.Thread(
        target=run_genesis_fix_job,
        args=(record["id"], record["workspace_id"]),
        daemon=True,
    ).start()
    return record


def run_genesis_fix_job(job_id: str, workspace_id: str) -> dict[str, Any]:
    """The fix-job runner body, workspace re-bound explicitly.

    Public and synchronous, mirroring :func:`run_genesis_job` exactly: a
    ``threading.Thread`` does not inherit the request's ContextVars, so the
    submitting workspace is re-bound here rather than trusted from
    ``current_workspace_id()`` (which raises on a bare worker thread).
    :func:`start_genesis_fix_job` is the only production caller.
    """
    with workspaces.bind_workspace(workspace_id):
        return _run_genesis_fix_job_locked(job_id)


def _fresh_shadow_model(path: Path, after_mtime: float | None) -> tuple[dict[str, Any], float]:
    """``build_shadow_model(path)``, forcing a fresh (non-stale-cached) parse.

    ``build_shadow_model`` is ``lru_cache``-keyed on ``(path, mtime)``
    (``services/agent/tools_network.py``); this runner parses the SAME path
    twice (before the write, after it), and ``services.agent.state.
    ArtifactManager._refresh_shadow_model``'s own docstring records the
    empirically-measured landmine this mirrors: two parses of one path can
    land at the same filesystem-mtime tick on this Windows dev box, which
    would otherwise silently hand back the STALE "before" parse for the
    "after" read. ``after_mtime`` is the mtime this same path was last parsed
    at (``None`` for the first parse); when the file's current mtime has not
    moved strictly past it, this nudges it forward with ``os.utime`` before
    parsing — the same nudge ``ArtifactManager`` performs, reimplemented
    small here rather than reached into (that class's cache-busting method is
    private, and this module does not otherwise depend on
    ``services.agent.state`` at all).

    Returns ``(shadow_model, mtime_parsed_at)`` so the caller can pass the
    second value back in as ``after_mtime`` for its own next call.
    """
    from services.agent.tools_network import build_shadow_model  # noqa: PLC0415 - see module note on lazy agent imports

    disk_mtime = path.stat().st_mtime
    next_mtime = disk_mtime
    if after_mtime is not None and next_mtime <= after_mtime:
        next_mtime = after_mtime + 0.001
        os.utime(path, (next_mtime, next_mtime))
    return build_shadow_model(path), next_mtime


def _build_fix_edit_session(working_inpx: Path) -> "VissimEditSession":
    """Construct the ``VissimEditSession`` for a fix job's one approved write.

    Thin, separately-named seam -- mirrors ``routers/assistant.py``'s own
    ``_build_edit_session`` precisely, for the same reason: tests
    ``monkeypatch.setattr(genesis_jobs, "_build_fix_edit_session", ...)``
    with a fake session, so the whole runner exercises real for everything
    except the one line that would otherwise need live Vissim COM. Production
    code path: unconditionally the real class.

    Imported function-local, not at module import time -- see this module's
    own docstring ("The one exception is a FIX job"): ``services.agent.
    edit_session`` imports ``services.vissim_com`` at ITS OWN top level, so
    importing it here at genesis_jobs.py's module scope would make a plain
    ``import services.genesis_jobs`` reach COM. This function (and the
    runner's own imports below) are the only places that import it, and only
    when a fix job actually RUNS.
    """
    from services.agent.edit_session import VissimEditSession  # noqa: PLC0415

    return VissimEditSession(working_inpx)


def _run_genesis_fix_job_locked(job_id: str) -> dict[str, Any]:
    record = get_job(job_id)
    try:
        derived = record.get("derived_from") or {}
        verb = str(derived.get("verb") or "")
        params = dict(derived.get("params") or {})
        artifact = record.get("artifact") or {}
        inpx_name = artifact.get("inpx")
        if not verb or not inpx_name:
            # Defensive only -- every job this module's own
            # create_genesis_fix_job produces carries both. A record missing
            # either did not come from here (or its registry entry was hand-
            # edited), and applying "no verb at all" would be a silent no-op
            # dressed as a fix.
            raise _FixApplyFailed(
                "missing_derivation",
                [
                    "This job carries no derived_from.verb/params or artifact.inpx to apply -- "
                    "it was not produced by create_genesis_fix_job."
                ],
            )

        work_dir = job_dir(job_id)
        working_inpx = work_dir / str(inpx_name)

        _update(job_id, status=STATUS_RUNNING, phase=PHASE_APPLYING_FIX)

        # Function-local: see this module's docstring and
        # _build_fix_edit_session's own note. Nothing above this line in this
        # function has touched COM; everything from here down runs on THIS
        # function's own caller's thread, which for production is always the
        # daemon thread start_genesis_fix_job started -- never a request
        # handler.
        from services.agent.edit_session import (  # noqa: PLC0415
            clear_active_edit_session,
            set_active_edit_session,
        )
        from services.agent.guardrails import GuardrailEngine  # noqa: PLC0415
        from services.agent.toolkit import dispatch as _dispatch_verb  # noqa: PLC0415

        session = _build_fix_edit_session(working_inpx)
        try:
            set_active_edit_session(session)
            try:
                before_model, before_mtime = _fresh_shadow_model(working_inpx, None)
                action = {"tool": verb, "params": dict(params)}
                engine = GuardrailEngine()

                # Preflight, exactly as the interactive approval loop runs it
                # (orchestrator.py's own guardrail sandwich) -- a REFUSE-tier
                # preflight finding skips dispatch entirely rather than
                # applying a write the same engine would refuse a human's turn
                # for.
                preflight_report = engine.preflight(action, before_model)
                if preflight_report.blocking:
                    raise _FixApplyFailed(
                        "preflight_blocked",
                        [
                            f"{f.constraint_id}: {f.messages.get('en') or f.messages.get('ko') or ''}"
                            for f in preflight_report.findings
                        ]
                        or ["preflight refused the approved verb"],
                    )

                # THE one dispatch. The tool's own function persists its write
                # internally (every write tool in tools_edit.py ends with
                # ``session.persist()``) -- there is no separate persist call
                # here, and none is needed.
                envelope = _dispatch_verb(verb, params)
                if envelope.get("status") != "ok":
                    raise _FixApplyFailed(
                        "dispatch_failed",
                        [str(envelope.get("message") or envelope.get("error_type") or "dispatch failed")],
                    )

                after_model, _after_mtime = _fresh_shadow_model(working_inpx, before_mtime)
                postwrite_report = engine.postwrite(
                    action, before_model, after_model, envelope.get("verification")
                )
                if postwrite_report.blocking:
                    raise _FixApplyFailed(
                        "postwrite_blocked",
                        [
                            f"{f.constraint_id}: {f.messages.get('en') or f.messages.get('ko') or ''}"
                            for f in postwrite_report.findings
                        ]
                        or ["postwrite verification refused the applied write"],
                    )
            finally:
                clear_active_edit_session()
        finally:
            session.close()

        # The clone's bytes changed under the write above -- re-hash before
        # gating, exactly as the ordinary generation runner does right before
        # its own call to gate_and_publish, so the record's artifact hash is
        # never stale relative to what is actually on disk.
        record = _update(
            job_id,
            phase=PHASE_GATING,
            artifact={
                **artifact,
                "sha256": _sha256(working_inpx),
                "bytes": working_inpx.stat().st_size,
            },
        )
        return gate_and_publish(job_id)
    except GenesisPublicationRecordError as exc:
        # Identical repair posture to _run_genesis_job_locked's own handling:
        # the dataset IS live, only the record write of that fact failed.
        logger.critical(
            "genesis_jobs: repairing fix job %s -- its dataset is registered but the publish "
            "record did not land on the first pass.",
            job_id,
        )
        try:
            return _update_with_retry(
                job_id,
                status=exc.status,
                phase=PHASE_DONE,
                progress=1.0,
                gate=exc.gate,
                publication=exc.publication,
                revocation=None,
                error=None,
            )
        except Exception:  # noqa: BLE001 - registry still unwritable; do not lie
            logger.critical(
                "genesis_jobs: fix job %s could not be repaired (registry still unwritable). "
                "Its active dataset IS live; the record must be reconciled by hand.",
                job_id,
                exc_info=True,
            )
            return get_job(job_id)
    except _FixApplyFailed as exc:
        # The parent is untouched (nothing above ever opens it for writing,
        # let alone this branch); no artifact of THIS job is registered,
        # because this return happens before gate_and_publish is ever
        # reached.
        logger.info(
            "genesis_jobs: fix job %s could not apply its verb (%s)", job_id, exc.step
        )
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={"code": f"fix_apply_failed:{exc.step}", "reasons": exc.reasons},
            error=f"fix_apply_failed:{exc.step}",
        )
    except Exception as exc:  # noqa: BLE001 - a job failure is data, not a crash
        logger.exception("genesis_jobs: fix job %s failed", job_id)
        try:
            already = get_job(job_id)
        except GenesisJobError:  # pragma: no cover - the record was just read
            already = {}
        if (already.get("refusal") or {}).get("code"):
            return already
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={
                "code": "fix_apply_failed:unexpected",
                "reasons": [f"{type(exc).__name__}: {exc}"],
            },
            error=f"fix_apply_failed:unexpected: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# V1's live evidence — attached, never manufactured
# ---------------------------------------------------------------------------


def attach_live_check(
    job_id: str,
    state: dict[str, Any],
    *,
    label: str | None = None,
    attested_by: str | None = None,
) -> dict[str, Any]:
    """Record an operator's attestation that a real Vissim loaded this artifact.

    NOT "prove Vissim ran" — see the module docstring. This arrives over HTTP,
    so it is a human claim carrying a name, and it is stored and displayed as
    one. ``attested_by`` is the authenticated caller; the router supplies it and
    it is not optional in practice.

    Two things are still enforced mechanically, and they are worth having:

    * **The evidence must be about THIS artifact.** ``gate.py`` hashes the file
      at the path the probe recorded and compares it with the gated file's
      digest; a state.json about a different build leaves V1 NOT_CHECKABLE with
      ``v1_oracle_names_another_network``. Evidence about another build is not
      weak evidence, it is other evidence.

      A first draft of this fix "hardened" that by rewriting the path to this
      job's own artifact before hashing. That was backwards and the existing
      tests caught it: forcing the path makes EVERY state.json hash our own
      file, so the comparison can never fail and the one mechanical check here
      is destroyed. The client-named path is what carries the claim; hashing it
      is how the claim is tested. It stays.
    * **Downgrades take the same road as upgrades.** A state recording
      ``load_ok: false`` turns V1 into a FAIL, the verdict into REFUSED, and the
      publication into a revocation. Attesting is not a one-way ratchet.
    """
    record = get_job(job_id)
    if record.get("status") not in GATED_STATUSES:
        raise GenesisJobError(
            f"Job {job_id} has no gated artifact to attach live evidence to "
            f"(status: {record.get('status')})."
        )

    oracle, gap = LoadOracle.from_probe_state(state, label=label)

    attestation = {
        "by": attested_by,
        "at": _now_iso(),
        "label": label,
        "basis": ATTESTATION_BASIS,
    }
    try:
        result = gate_and_publish(
            job_id, live_oracle=oracle, live_oracle_gap=gap, attestation=attestation
        )
    except (GenesisJobNotFoundError, datasets.DatasetStorageError, GenesisJobError):
        raise
    except Exception as exc:  # noqa: BLE001
        # The worker-thread path wraps its whole body so a crash always reaches a
        # terminal, recorded state. This path had no such guarantee: a throw
        # inside run_gate left `phase=gating` beside a stale terminal status —
        # a record simultaneously claiming to be mid-run and finished, which no
        # other path produces and nothing cleans up — and surfaced as a raw 500.
        logger.exception("genesis: re-gate failed for job %s", job_id)
        _update(
            job_id,
            phase=PHASE_DONE,
            regate_error={
                "code": "regate_failed",
                "message": str(exc),
                "at": _now_iso(),
                "attested_by": attested_by,
            },
        )
        raise GenesisJobError(
            f"Re-gating job {job_id} failed; the job keeps its previous verdict. "
            f"Cause: {exc}"
        ) from exc
    return result


# ---------------------------------------------------------------------------
# V1's live evidence, PRODUCED rather than attached — the live-check JOB (#33)
# ---------------------------------------------------------------------------


def create_live_check_job(
    parent_job_id: str, *, requested_by: str | None
) -> dict[str, Any]:
    """Register a live-check job WITHOUT running it — the clone step.

    A live-check job answers ONE question about its parent: does a real PTV
    Vissim open that artifact, and what does it hold afterwards? It is the
    same operation ``tools/genesis_open_live_check.py`` performs from a console
    (the very same function — ``services.vissim_open_probe.probe_open``), moved
    behind a button so closing V1 stops depending on an operator remembering to
    run a CLI. What it does NOT do is judge: the evidence goes to the
    UNMODIFIED gate through :func:`gate_and_publish`, and V1's verdict is
    whatever ``gate.py`` makes of it.

    **Eligibility is one fact, and it is NAMED.** Only a parent that HAS an
    emitted ``.inpx`` can be live-checked, because the probe's whole input is
    that file:

    * a parent whose status is not one of :data:`GATED_STATUSES` (queued, still
      running, ``error`` — or a live-check job itself, which never gates
      anything) is refused with ``genesis_live_check_parent_not_gated``;
    * a parent that is gated but whose artifact is gone from disk is refused
      with ``genesis_live_check_artifact_missing``.

    Both are :class:`GenesisRefused` — a named refusal the UI renders as a
    sentence, never a greyed-out button (this project's refusal-is-information
    rule). Neither creates a job directory: the id is minted only after both
    pass.

    The parent's artifact is then CLONED into the new job's own work directory
    under the same base name, the same way :func:`create_genesis_fix_job` clones
    it and for the same two reasons — CLAUDE.md forbids opening a live dataset's
    ``.inpx`` in place, and the clone's sha256 is computed from the bytes that
    were ACTUALLY copied (after ``copyfile`` returns, never a second read of the
    source) so the lineage hash cannot disagree with itself.

    That clone is also what makes the evidence admissible: it is byte-identical
    to the parent's artifact, so the sha256 ``LoadOracle.from_probe_state``
    computes over the path the probe recorded equals the digest ``run_gate``
    computes over the parent's own file. If the parent's artifact changes
    between this call and the run, the digests diverge and ``gate.py`` names the
    evidence as being about another network — which is the correct answer, and
    one this module must never paper over.

    Raises:
        GenesisJobNotFoundError: no such parent job in this workspace.
        GenesisRefused: the parent has no gated artifact to open.
    """
    workspace_id = workspaces.current_workspace_id()
    parent = get_job(parent_job_id)
    if parent.get("status") not in GATED_STATUSES:
        raise GenesisRefused(
            "genesis_live_check_parent_not_gated",
            [
                f"Job {parent_job_id} is {parent.get('status')!r}, which carries no gated "
                f"network file. A live check opens the generation's own .inpx in a real "
                "Vissim, so there has to be one -- wait for the generation to reach a "
                "verdict, or start a new one."
            ],
        )
    try:
        parent_artifact = artifact_path(parent_job_id, "inpx")
    except GenesisJobNotFoundError as exc:
        raise GenesisRefused(
            "genesis_live_check_artifact_missing",
            [
                f"Job {parent_job_id} reached a verdict but its .inpx is no longer in the "
                f"job's work directory, so there is nothing to open. [{exc}]"
            ],
        ) from exc

    job_id = uuid4().hex[:12]
    work_dir = job_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    name = str(parent.get("name") or "live-check")
    clone_path = work_dir / f"{name}.inpx"
    # A plain copy. The parent's own artifact is read once here and never
    # touched again by anything in this module; the COM session below opens
    # THIS clone, in the NEW job's own work dir.
    shutil.copyfile(parent_artifact, clone_path)
    clone_sha256 = _sha256(clone_path)

    record = {
        "id": job_id,
        "workspace_id": workspace_id,
        "name": name,
        "status": STATUS_QUEUED,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "submitted_by": requested_by,
        "options": {},
        # No .osm of its own: this job re-uses the parent's artifact bytes.
        "source_osm": None,
        "network": None,
        "artifact": {
            "inpx": clone_path.name,
            "compliance_md": None,
            # Named by convention, absent on disk: this job never emits a
            # census and never gates its own clone.
            "genesis_json": f"{name}.genesis.json",
            "sha256": clone_sha256,
            "bytes": clone_path.stat().st_size,
        },
        "layers": None,
        # This job has NO verdict of its own and never will -- the verdict this
        # run affects is the PARENT's. A ``gate`` here would invite a reader to
        # believe otherwise.
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
        # Filled by the runner: what the probe measured and what the parent's
        # re-gate then said.
        "live_check": None,
        "derived_from": {
            "kind": LIVE_CHECK_LINEAGE_KIND,
            "job_id": parent_job_id,
            "requested_by": requested_by,
            "requested_at": _now_iso(),
            "parent_sha256": clone_sha256,
        },
    }
    _upsert(record)
    return record


def start_live_check_job(parent_job_id: str, **kwargs: Any) -> dict[str, Any]:
    """:func:`create_live_check_job`, then run it on a daemon worker thread.

    The same split :func:`start_genesis_fix_job` uses, for the same
    non-negotiable reason: cloning the artifact and minting the record is fast
    synchronous request-thread work, while opening Vissim is a live COM turn
    that CLAUDE.md forbids inside a request handler. ``routers/genesis.py``
    calls this and returns the queued record; the caller polls
    ``GET /jobs/{id}``.
    """
    record = create_live_check_job(parent_job_id, **kwargs)
    threading.Thread(
        target=run_live_check_job,
        args=(record["id"], record["workspace_id"]),
        daemon=True,
    ).start()
    return record


def run_live_check_job(job_id: str, workspace_id: str) -> dict[str, Any]:
    """The live-check runner body, workspace re-bound explicitly.

    Public and synchronous, mirroring :func:`run_genesis_fix_job`: a
    ``threading.Thread`` does not inherit the request's ContextVars, so the
    submitting workspace is re-bound here rather than trusted from
    ``current_workspace_id()``. :func:`start_live_check_job` is the only
    production caller; tests drive ``create_live_check_job`` +
    ``run_live_check_job`` directly for a synchronous result.
    """
    with workspaces.bind_workspace(workspace_id):
        return _run_live_check_job_locked(job_id)


def _build_live_check_probe():
    """The probe callable a live-check job runs — THE COM SEAM.

    Thin and separately named for exactly the reason
    :func:`_build_fix_edit_session` is: tests ``monkeypatch.setattr(
    genesis_jobs, "_build_live_check_probe", ...)`` with a fake that returns a
    canned probe record, so the whole runner — the clone, the state file, the
    admissibility rule, the REAL gate, the parent's re-gate, the archive — runs
    for real, and only the one line that needs live Vissim does not. Production
    always returns the real probe.

    Imported function-local, like every other COM-adjacent import in this
    module: ``services.vissim_open_probe`` is itself COM-free at import time,
    but keeping the import here means this module's import graph does not depend
    on that staying true.
    """
    from services.vissim_open_probe import probe_open  # noqa: PLC0415

    return probe_open


def _run_live_check_job_locked(job_id: str) -> dict[str, Any]:
    record = get_job(job_id)
    work_dir = job_dir(job_id)
    try:
        derived = record.get("derived_from") or {}
        parent_job_id = str(derived.get("job_id") or "")
        artifact = record.get("artifact") or {}
        inpx_name = artifact.get("inpx")
        if derived.get("kind") != LIVE_CHECK_LINEAGE_KIND or not parent_job_id or not inpx_name:
            # Defensive only -- every job create_live_check_job produces carries
            # all three. A record missing any of them did not come from here,
            # and guessing a parent to re-gate is the last thing this should do.
            raise _LiveCheckFailed(
                "missing_derivation",
                [
                    "This job carries no live-check derived_from/artifact.inpx -- it was not "
                    "produced by create_live_check_job."
                ],
            )

        clone_path = work_dir / str(inpx_name)
        # The run label. Deterministic and job-scoped, so the oracle is read
        # back BY NAME rather than by from_probe_state's newest-run fallback --
        # a job must never be able to pick up another run's evidence.
        label = f"live_check_{job_id}"

        _update(job_id, status=STATUS_RUNNING, phase=PHASE_LIVE_CHECKING)

        # THE COM TURN. On this worker thread, on this job's own clone, through
        # the seam above -- never in a request handler, never on the parent's
        # file, never attached to a running Vissim (probe_open's own
        # _connect_controller passes attach_to_running=False).
        probe = _build_live_check_probe()
        started_utc = _now_iso()
        try:
            com = probe(clone_path, label)
        except Exception as exc:  # noqa: BLE001 - recorded as a named refusal below
            raise _LiveCheckFailed(
                "probe_failed", [f"{type(exc).__name__}: {exc}"]
            ) from exc

        # The evidence file, in EXACTLY the shape LoadOracle.from_probe_state
        # reads -- which is the shape tools/genesis_open_live_check.py's own
        # state.json has, because both are written from the same probe record.
        # Written BEFORE anything is judged, and whatever the probe said: a
        # refused load is evidence, and evidence that only exists when it is
        # convenient is not evidence.
        state = {
            "probe": "genesis_live_check_job",
            "job_id": job_id,
            "parent_job_id": parent_job_id,
            "runs": {
                label: {
                    "label": label,
                    "network": str(clone_path),
                    "started_utc": (com or {}).get("started_utc") or started_utc,
                    "finished_utc": (com or {}).get("finished_utc") or _now_iso(),
                    "run": {"com": com},
                }
            },
        }
        (work_dir / LIVE_CHECK_STATE_FILENAME).write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )

        if not isinstance(com, dict) or com.get("fatal_error") or "load_ok" not in com:
            raise _LiveCheckFailed(
                "probe_produced_no_verdict",
                [
                    str(
                        (com or {}).get("fatal_error")
                        or "The probe returned no load result at all."
                    )
                ],
            )

        oracle, gap = LoadOracle.from_probe_state(state, label=label)

        _update(job_id, phase=PHASE_GATING)
        parent = gate_and_publish(
            parent_job_id,
            live_oracle=oracle,
            live_oracle_gap=gap,
            attestation={
                "by": derived.get("requested_by"),
                "at": _now_iso(),
                "label": label,
                # NOT operator_attested: this record was written by this process
                # from what LoadNet returned, not uploaded by a caller.
                "basis": LIVE_CHECK_BASIS,
            },
            regate_source={
                "source": "live_check_job",
                "job_id": job_id,
                "at": _now_iso(),
            },
        )
        parent_gate = parent.get("gate") or {}
        return _update(
            job_id,
            status=STATUS_CHECKED,
            phase=PHASE_DONE,
            progress=1.0,
            live_check={
                "parent_job_id": parent_job_id,
                "label": label,
                "state_file": LIVE_CHECK_STATE_FILENAME,
                "load_ok": com.get("load_ok"),
                "load_error": com.get("load_error"),
                "save_ok": com.get("save_ok"),
                "vissim_version": (
                    ((com.get("version") or {}).get("att_values") or {}).get("Version")
                ),
                # Read back off the parent's own summary rather than asserted
                # here: whether the evidence was ADMITTED is gate.py's answer to
                # its sha256 identity rule, not this runner's opinion.
                "admitted": (parent_gate.get("live_evidence") or {}).get("admitted"),
                "gap_codes": (parent_gate.get("live_evidence") or {}).get("gap_codes") or [],
                "parent_status": parent.get("status"),
                "parent_verdict": parent_gate.get("verdict"),
                "previous_gate_archived": (parent_gate.get("regate") or {}).get(
                    "previous_gate_archived"
                ),
            },
        )
    except GenesisPublicationRecordError as exc:
        # The PARENT's dataset is live but its publish record did not land. The
        # parent is repaired the same way both other runners repair it; this
        # job then reports itself as checked, because its own work (producing
        # the evidence) genuinely finished.
        logger.critical(
            "genesis_jobs: live-check job %s re-gated its parent, whose dataset is registered "
            "but whose publish record did not land on the first pass.",
            job_id,
        )
        try:
            _update_with_retry(
                exc.job_id,
                status=exc.status,
                phase=PHASE_DONE,
                progress=1.0,
                gate=exc.gate,
                publication=exc.publication,
                revocation=None,
                error=None,
            )
        except Exception:  # noqa: BLE001 - registry still unwritable; do not lie
            logger.critical(
                "genesis_jobs: parent %s of live-check job %s could not be repaired "
                "(registry still unwritable); it must be reconciled by hand.",
                exc.job_id,
                job_id,
                exc_info=True,
            )
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={
                "code": "live_check_failed:parent_publication_record",
                "reasons": [str(exc)],
            },
            error="live_check_failed:parent_publication_record",
        )
    except datasets.DatasetStorageError as exc:
        # The parent's re-gate RAN and its verdict is recorded; only promoting
        # the artifact into the workspace's dataset slot failed (on Windows,
        # anything holding ``uploads/current`` open — see services/datasets.py).
        # gate_and_publish has already written that onto the PARENT with its own
        # ``publish_failed`` code, so this job names the same cause rather than
        # calling it unexpected.
        logger.error(
            "genesis_jobs: live-check job %s re-gated its parent, but the dataset promote "
            "was refused (%s: %s)",
            job_id,
            type(exc).__name__,
            exc,
        )
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={
                "code": "live_check_failed:parent_publish_refused",
                "reasons": [
                    "The live check produced its evidence and the verdict was re-decided, but "
                    f"the network could not be registered as the project's dataset. [{exc}]"
                ],
            },
            error=f"live_check_failed:parent_publish_refused: {exc}",
        )
    except _LiveCheckFailed as exc:
        # Nothing was re-gated: this branch is only ever reached before
        # gate_and_publish is called, so the parent keeps the verdict it had.
        logger.info(
            "genesis_jobs: live-check job %s produced no evidence (%s)", job_id, exc.step
        )
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={"code": f"live_check_failed:{exc.step}", "reasons": exc.reasons},
            error=f"live_check_failed:{exc.step}",
        )
    except Exception as exc:  # noqa: BLE001 - a job failure is data, not a crash
        logger.exception("genesis_jobs: live-check job %s failed", job_id)
        try:
            already = get_job(job_id)
        except GenesisJobError:  # pragma: no cover - the record was just read
            already = {}
        if (already.get("refusal") or {}).get("code"):
            return already
        return _update(
            job_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={
                "code": "live_check_failed:unexpected",
                "reasons": [f"{type(exc).__name__}: {exc}"],
            },
            error=f"live_check_failed:unexpected: {type(exc).__name__}: {exc}",
        )


def reconcile_interrupted_jobs() -> list[str]:
    """Terminate jobs a dead process left claiming to run. Called once at boot.

    A generation runs in a worker thread inside the API process, so a crash,
    a restart or a deploy leaves ``queued``/``running`` records that nothing
    will ever advance: the thread that owned them is gone. The UI polls them
    forever and an operator watches a progress bar that cannot move.

    They are moved to ``error`` — never to a gated status — because that is the
    honest reading: no gate verdict was ever reached for them, so there is no
    artifact anyone should trust. The reason is named so the record says what
    happened rather than implying the .osm was bad.

    Iterates EVERY workspace: jobs live per-workspace, and a sweep that only
    saw the default one would quietly leave every client project's interrupted
    jobs spinning. Best-effort throughout — a boot must never fail over
    bookkeeping, which is why each workspace is guarded separately.
    """
    reconciled: list[str] = []
    try:
        entries = workspaces.list_workspaces()
    except Exception:  # noqa: BLE001 - a boot sweep never blocks startup
        logger.exception("genesis: could not enumerate workspaces to reconcile jobs")
        return reconciled

    for entry in entries:
        workspace_id = str(entry.get("id") or "")
        if not workspace_id:
            continue
        try:
            with workspaces.bind_workspace(workspace_id):
                for job in list_jobs():
                    if job.get("status") in TERMINAL_STATUSES:
                        continue
                    job_id = str(job.get("id") or "")
                    if not job_id:
                        continue
                    _update(
                        job_id,
                        status=STATUS_ERROR,
                        phase=PHASE_DONE,
                        error={
                            "code": "interrupted_by_restart",
                            "message": (
                                "The process running this generation stopped before it "
                                "reached a verdict. No artifact was gated; start it again."
                            ),
                            "at": _now_iso(),
                        },
                    )
                    reconciled.append(f"{workspace_id}:{job_id}")
        except Exception:  # noqa: BLE001 - one bad workspace must not stop the rest
            logger.exception("genesis: could not reconcile jobs for workspace %s", workspace_id)
    if reconciled:
        logger.info("genesis: reconciled %d interrupted job(s) at startup", len(reconciled))
    return reconciled


# ---------------------------------------------------------------------------
# Treating a finding by RE-DERIVING it — the regenerate JOB (#38)
# ---------------------------------------------------------------------------

#: The lineage ``kind`` on a REGENERATE job's ``derived_from``, beside
#: :data:`LIVE_CHECK_LINEAGE_KIND`. A regenerate job is an ordinary generation
#: in every other respect -- same runner, same emitter, same unmodified gate --
#: and this key is how a reader tells one that was RE-derived from a parent's
#: source from one somebody started by uploading a file.
REGENERATE_LINEAGE_KIND = "regenerate"

#: The lineage ``kind`` on a unit #42 DEAD-END EXCLUSION job. It rides the very
#: same machine as :data:`REGENERATE_LINEAGE_KIND` — the parent's sha-verified
#: source bytes, the parent's own options, the normal runner, the unmodified
#: judge — and differs in exactly one declared parameter: a way a human
#: CONFIRMED the road really ends at is excluded by name. A separate kind rather
#: than a flag on the other, because "re-derived unchanged" and "re-derived
#: without a way somebody took responsibility for removing" are two different
#: claims and a reader must never have to infer which one they hold.
DEAD_END_EXCLUSION_LINEAGE_KIND = "dead_end_exclusion"


def _regeneration_source_preflight(parent_job_id: str) -> tuple[dict[str, Any], Path, str]:
    """The four refusals EVERY re-derivation of a parent's own source shares —
    ``(parent record, parent source path, recorded sha)`` when all four pass.

    Extracted (unit #42) from :func:`create_regenerate_job` so its sibling
    :func:`create_dead_end_exclusion_job` obeys the SAME doors rather than
    growing its own near-copies of them. The refusal codes and their sentences
    are unit #38's, unchanged and deliberately so: a caller who learned them
    from the regenerate route reads the identical answer from the exclusion
    route, and a unit that quietly relaxed one of them for its own path would be
    exactly the bypass #38's docstring warns about. Each door, and why:

    * ``genesis_regenerate_parent_not_gated`` — no verdict, so no finding to treat;
    * ``genesis_regenerate_parent_has_side_inputs`` — a re-derivation carries
      GEOMETRY ONLY, and minting a thinner artifact that looks complete is the
      completed-but-wrong outcome this system exists to refuse;
    * ``genesis_regenerate_source_missing`` — no stored source bytes to re-read;
    * ``genesis_regenerate_source_sha_mismatch`` — the bytes no longer hash to
      the digest the parent RECORDED, so "the same source" is a claim that would
      be assumed rather than verified.

    Eligibility — WHICH finding earns the re-derivation — is deliberately NOT
    here: that is the one question the two callers answer differently, and each
    raises its own named refusal for it.
    """
    parent = get_job(parent_job_id)
    if parent.get("status") not in GATED_STATUSES:
        raise GenesisRefused(
            "genesis_regenerate_parent_not_gated",
            [
                f"Job {parent_job_id} is {parent.get('status')!r}, which carries no gate "
                "verdict. A regeneration treats a finding the gate already made -- wait for "
                "the generation to reach a verdict, or start a new one."
            ],
        )

    # Refuter finding F7 (2026-08-15): a regeneration re-runs osm->draft->emit
    # with side_inputs=None, so regenerating a COMPOSED parent would mint an
    # artifact that silently lacks the demand/signals/fleet its parent carried
    # -- structurally indistinguishable from a job that never had them. Until a
    # unit decides how side inputs travel through a regeneration, the honest
    # answer is a refusal that says exactly that.
    if parent.get("side_inputs"):
        raise GenesisRefused(
            "genesis_regenerate_parent_has_side_inputs",
            [
                f"Job {parent_job_id} was composed with side inputs "
                f"({', '.join(sorted(parent['side_inputs']))}), and a regeneration re-derives "
                "GEOMETRY ONLY -- the regenerated artifact would silently lack what the "
                "parent carried. Carrying side inputs through a regeneration is a decision "
                "no unit has made yet, so this refuses rather than minting a thinner "
                "artifact that looks complete."
            ],
        )

    source_record = parent.get("source_osm") or {}
    try:
        parent_source = artifact_path(parent_job_id, "source_osm")
    except GenesisJobNotFoundError as exc:
        raise GenesisRefused(
            "genesis_regenerate_source_missing",
            [
                f"Job {parent_job_id} has no stored source .osm to re-derive from. [{exc}] "
                "A regeneration re-reads the SAME source bytes; a job that never had a "
                "source of its own (a fix job, a live check) cannot be regenerated -- "
                "regenerate the generation those were derived from instead."
            ],
        ) from exc

    recorded_sha = str(source_record.get("sha256") or "")
    actual_sha = _sha256(parent_source)
    if not recorded_sha or actual_sha != recorded_sha:
        raise GenesisRefused(
            "genesis_regenerate_source_sha_mismatch",
            [
                f"Job {parent_job_id}'s stored source .osm hashes to {actual_sha}, and its "
                f"record says {recorded_sha or '(nothing)'}. A regeneration claims to use the "
                "SAME source bytes, so the claim is verified rather than assumed -- and this "
                "file is not the one the parent was judged on."
            ],
        )
    return parent, parent_source, recorded_sha


def _freeze_parent_source(job_id: str, parent_source: Path, recorded_sha: str) -> Path:
    """Copy the parent's own source bytes into ``job_id``'s work dir and PROVE
    the copy is the same file. Unit #38's own two steps, shared with #42.

    The parent's file is opened for READING here and nowhere else — the
    byte-untouched rule every sibling job obeys."""
    work_dir = job_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    frozen_osm = work_dir / f"source{parent_source.suffix.lower() or '.osm'}"
    shutil.copyfile(parent_source, frozen_osm)
    copied_sha = _sha256(frozen_osm)
    if copied_sha != recorded_sha:  # pragma: no cover - a copy that changed bytes
        raise GenesisRefused(
            "genesis_regenerate_source_sha_mismatch",
            [
                f"The copy of the parent's source hashes to {copied_sha}, not the "
                f"recorded {recorded_sha}. Nothing was started."
            ],
        )
    return frozen_osm


def regenerable_findings(job_id: str) -> list[dict[str, Any]]:
    """Every finding in ``job_id``'s STORED verdict whose only honest treatment
    is a re-derivation — :data:`~services.agent.genesis_fixes.REGENERATE_WITH_FIX`.

    Built from exactly the pieces the fixes-listing route uses (the stored
    ``gate.json`` through :func:`iter_stored_gate_findings`, evidence through
    :func:`derive_fix_targets`, the registry through ``propose_genesis_fix``,
    and the eligibility measurement through
    ``genesis_fixes.regeneration_disposition``), so this module's own
    eligibility gate and what the UI showed the approver cannot disagree. Empty
    list for a job with no stored verdict, an unreadable one, or one whose
    findings are all treatable some other way -- never a raise: the caller
    turns emptiness into a NAMED refusal, which is a different sentence from a
    crash.
    """
    from services.agent import genesis_fixes as agent_genesis_fixes  # noqa: PLC0415

    stored = job_dir(job_id) / GATE_JSON_FILENAME
    if not stored.is_file():
        return []
    try:
        raw = json.loads(stored.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    draft = job_draft_or_none(job_id)
    census = draft.metadata if draft is not None else None
    found: list[dict[str, Any]] = []
    for check_id, origin, finding in iter_stored_gate_findings(raw):
        targets = derive_fix_targets(
            finding["code"], finding, draft=draft, params=None, job_id=job_id
        )
        proposal = agent_genesis_fixes.propose_genesis_fix(
            finding["code"], finding=finding, census=census, targets=targets
        )
        disposition = agent_genesis_fixes.regeneration_disposition(
            finding["code"], proposal, converter_fixes=CONVERTER_FIXES
        )
        if disposition is None:
            continue
        found.append(
            {
                "check_id": check_id,
                "origin": origin,
                "code": finding["code"],
                "subject": finding["subject"],
                "derivation_reason_code": disposition["derivation_reason_code"],
            }
        )
    return found


def create_regenerate_job(
    parent_job_id: str, *, requested_by: str | None, reason: str | None = None
) -> dict[str, Any]:
    """Register a REGENERATION of ``parent_job_id`` — the same source, the
    corrected converter — WITHOUT running it.

    **Nothing is invented: the artifact is re-derived from the same source.**
    Unit #34 measured that every real ``v7_links_share_one_polyline`` finding's
    draft twins are byte-identical, i.e. the corruption predates the draft and
    no census-derived edit can clear it; unit #38 found what upstream did it (a
    source declaring one way id twice —
    :data:`services.genesis.osm.SKIP_REDECLARED_IDENTICALLY`) and fixed the
    converter. This function is how an artifact refused under that finding gets
    treated *honestly*: the parent's own ``source.osm`` bytes are copied into a
    new job and run through the NORMAL pipeline
    (:func:`run_genesis_job` — same draft, same emitter, same UNMODIFIED gate),
    and the new job gates on its own merits. A regeneration is not a promise
    that the finding clears; it is a re-derivation whose verdict is its own.

    **The parent is byte-untouched**, exactly as every sibling job in B5-2 is
    required to leave it: this function only READS the parent's source file and
    its stored verdict. Nothing is written into the parent's work directory.

    **Eligibility is measured, and every refusal is NAMED** (never a greyed-out
    button):

    * ``genesis_regenerate_parent_not_gated`` — the parent holds no verdict
      (:data:`GATED_STATUSES`), so there is no finding to treat;
    * ``genesis_regenerate_source_missing`` — the parent has no stored source
      ``.osm`` (a fix job and a live-check job both carry ``source_osm: None``
      by construction, and a regeneration of a regeneration's parent needs the
      file itself, not a lineage pointer);
    * ``genesis_regenerate_parent_has_side_inputs`` — the parent was composed
      with demand/signals/fleet side inputs, and a regeneration re-derives
      geometry only; minting an artifact that silently lacks what its parent
      carried is exactly the completed-but-wrong outcome this system refuses.
      How side inputs travel through a regeneration is an undecided follow-up;
    * ``genesis_regenerate_source_sha_mismatch`` — the stored bytes no longer
      hash to the digest the parent RECORDED for them. The lineage claim is
      "the same source", so it is verified rather than assumed; a source that
      changed under a job would make the regenerated artifact a comparison
      against something that no longer exists;
    * ``genesis_regenerate_not_eligible`` — the parent's verdict carries no
      finding whose disposition is ``REGENERATE_WITH_FIX``
      (:func:`regenerable_findings`). This is the one that keeps the path
      honest: a parent whose v7 findings the CENSUS can correct has unit #34's
      ``set_link_geometry`` path and the refusal says so, and a parent with no
      such findings at all is not made better by re-running the same pipeline.

    ``reason`` is the requester's own sentence, carried into the lineage
    verbatim and never invented here.
    """
    workspace_id = workspaces.current_workspace_id()
    # The four doors every re-derivation shares (unit #38's own, extracted for
    # #42's sibling path so neither can drift from the other).
    parent, parent_source, recorded_sha = _regeneration_source_preflight(parent_job_id)
    source_record = parent.get("source_osm") or {}

    eligible = regenerable_findings(parent_job_id)
    if not eligible:
        raise GenesisRefused(
            "genesis_regenerate_not_eligible",
            [
                f"Job {parent_job_id}'s verdict carries no finding whose treatment is a "
                "re-derivation. A finding the census itself can correct is treated by the "
                "approved edit path (POST .../fixes/apply) instead, and re-running the same "
                "source through the same pipeline would change nothing for the rest."
            ],
        )

    job_id = uuid4().hex[:12]
    frozen_osm = _freeze_parent_source(job_id, parent_source, recorded_sha)
    copied_sha = _sha256(frozen_osm)

    record = {
        "id": job_id,
        "workspace_id": workspace_id,
        "name": str(parent.get("name") or "regenerated"),
        "status": STATUS_QUEUED,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "submitted_by": requested_by,
        # The parent's OWN options, carried verbatim: a regeneration that
        # quietly changed exclude_service would be comparing two different
        # questions and calling the difference a fix.
        "options": dict(parent.get("options") or {}),
        # ...and, for the same reason, the parent's own declared exclusions
        # (unit #42). A regeneration re-derives with the SAME inputs, and a
        # confirmed real-world dead end is one of them: dropping it here would
        # silently REVERSE a decision a named person took responsibility for,
        # and the reversal would be invisible in a record whose whole claim is
        # "the same source". Key-ABSENT when the parent declared none, so an
        # ordinary regeneration's record is byte-for-byte what it always was.
        **(
            {"excluded_ways": [dict(entry) for entry in parent["excluded_ways"]]}
            if parent.get("excluded_ways")
            else {}
        ),
        "source_osm": {
            "filename": str(source_record.get("filename") or frozen_osm.name),
            "bytes": frozen_osm.stat().st_size,
            "sha256": copied_sha,
            "stored_as": frozen_osm.name,
        },
        # Side inputs are NOT carried. A regeneration re-derives the NETWORK
        # from its source; the demand/signals layers a parent was composed with
        # are separate uploads this function has no claim on, and silently
        # re-attaching them would make the two artifacts differ in more ways
        # than the converter fix this job exists to demonstrate. Named here
        # rather than left as an absence -- see the unit's residuals.
        "side_inputs": None,
        "network": None,
        "artifact": None,
        "layers": None,
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
        "derived_from": {
            "kind": REGENERATE_LINEAGE_KIND,
            "job_id": parent_job_id,
            "requested_by": requested_by,
            "requested_at": _now_iso(),
            # The digest of the bytes ACTUALLY copied, proven equal to the
            # parent's recorded one above -- so the lineage cannot claim a
            # sameness nothing checked.
            "source_sha256": copied_sha,
            "parent_source_sha256": recorded_sha,
            "converter_fixes": [dict(entry) for entry in CONVERTER_FIXES],
            "findings": eligible,
            # Unit #42: what this re-derivation inherits besides the bytes. A
            # regeneration that carried a human's exclusion without SAYING so
            # would leave a reader of the lineage unable to explain why the
            # regenerated network is missing a road the source declares.
            **(
                {"inherited_exclusions": [dict(e) for e in parent["excluded_ways"]]}
                if parent.get("excluded_ways")
                else {}
            ),
            "reason": reason,
        },
    }
    _upsert(record)
    return record


def start_regenerate_job(parent_job_id: str, **kwargs: Any) -> dict[str, Any]:
    """:func:`create_regenerate_job`, then run it on a daemon worker thread.

    The regeneration runs through :func:`run_genesis_job` -- the SAME runner an
    ordinary generation uses, deliberately, so nothing about a re-derived
    artifact is produced by a special path the ordinary one does not have.
    """
    record = create_regenerate_job(parent_job_id, **kwargs)
    threading.Thread(
        target=run_genesis_job,
        args=(record["id"], record["workspace_id"]),
        daemon=True,
    ).start()
    return record


# ---------------------------------------------------------------------------
# Unit #42 — the dead-end EXCLUSION job. The same machine as the regeneration
# above, plus exactly one declared parameter.
# ---------------------------------------------------------------------------


def create_dead_end_exclusion_job(
    parent_job_id: str,
    *,
    finding_code: str,
    subject: str | None,
    way_id: int | str | None = None,
    basis: str,
    acknowledged_collateral_way_ids: Sequence[Any] | None,
    confirmed_by: str | None,
) -> dict[str, Any]:
    """Register a re-derivation of ``parent_job_id`` WITHOUT the one way a human
    confirmed the road really ends at — the unit #42 treatment. Does not run it.

    **Everything unit #38's regeneration is**, and one thing more. The parent's
    own ``source.osm`` bytes are copied (sha-verified against the parent's own
    recorded digest), the parent's ``options`` ride verbatim, the NORMAL runner
    and the UNMODIFIED judge do the rest, the parent is byte-untouched, and the
    sibling gates on its own merits. The four shared doors are literally the
    same code (:func:`_regeneration_source_preflight`) rather than a near-copy,
    so this path cannot be a way around them.

    **The source file is never edited.** The exclusion is a DECLARATIVE
    parameter on the new job's record (``excluded_ways``), applied by
    :func:`_run_genesis_job_locked` to the PARSED extract through
    :meth:`~services.genesis.osm.OsmExtract.without_ways`. The stored bytes stay
    sha-identical to the parent's, because a source is evidence: an exclusion
    that rewrote it would destroy the very thing a later reader would need to
    disagree with the exclusion.

    **The exclusion is a ledger entry, not a deletion.** Every excluded way
    leaves a :data:`~services.genesis.osm.SKIP_CONFIRMED_REAL_WORLD_DEAD_END`
    skip behind whose detail names WHO confirmed it, WHEN (server-stamped),
    against which finding, and their own words verbatim — so it lands in the
    sibling's ``*.genesis.json`` census and its compliance report, which is
    B5-1's rule for an exclusion applied to itself.

    **Refusals, each named** (beyond the four shared ones, which keep unit #38's
    own codes):

    * ``genesis_dead_end_finding_not_in_parent_verdict`` — the confirmation
      names a finding the parent's stored verdict does not carry, the same door
      ``create_genesis_fix_job`` closed against exploit #3. Ambiguity resolves
      the same way too: a code appearing twice needs its ``subject``;
    * ``genesis_dead_end_finding_ambiguous_without_subject`` — that code appears
      more than once and no subject named which;
    * ``genesis_dead_end_not_confirmable`` — this module's OWN re-derivation
      (:func:`derive_v3_dead_end_confirmation`) does not offer the confirmation,
      and the refusal quotes its measured reason. This is the door that carries
      the evidence hierarchy: a finding whose SOURCE testifies to the connection
      refuses here under ``source_testifies_the_connection``, however confident
      the person pressing is. It is also where an incomplete or mismatched
      confirmation lands, and where a collateral set the reader never saw is
      refused rather than reconciled;
    * ``genesis_dead_end_already_excluded`` — the parent already carries this
      way as a confirmed dead end, so its network does not contain it.

    **A chain of confirmations ACCUMULATES.** The parent's own
    ``excluded_ways`` lead the new record's list and this confirmation appends,
    so a second confirmation on a sibling cannot silently re-introduce the way
    the first one's confirmer took responsibility for removing. Unit #38's
    plain regeneration carries them forward for the same reason, naming them in
    its lineage as ``inherited_exclusions``.

    ``confirmed_by`` is the SIGNED-IN session's own name and the timestamp is
    this server's clock — neither is a field any request model offers, exactly
    as unit #35's attestation and the phase-diagram attestation before it.
    """
    workspace_id = workspaces.current_workspace_id()
    parent, parent_source, recorded_sha = _regeneration_source_preflight(parent_job_id)
    source_record = parent.get("source_osm") or {}

    raw_parent_gate = json.loads(
        (job_dir(parent_job_id) / GATE_JSON_FILENAME).read_text(encoding="utf-8")
    )
    # ``genesis_fix_finding_ambiguous_without_subject`` propagates from here
    # UNRENAMED: it is a statement about addressing a finding, not about the
    # treatment being applied, and a caller who learned it from the apply route
    # must read the same answer here. Unit #41 measured the state it guards on
    # the real screen -- V3 emits the same (code, subject) twice for 대덕대로
    # w52496957s1 -- so it is a live door on this path too.
    finding = _finding_in_stored_gate(raw_parent_gate, finding_code, subject=subject)
    if finding is None:
        looked_for = (
            f"Finding code {finding_code!r} with subject {subject!r}"
            if subject is not None
            else f"Finding code {finding_code!r}"
        )
        raise GenesisRefused(
            "genesis_dead_end_finding_not_in_parent_verdict",
            [
                f"{looked_for} does not appear anywhere in job {parent_job_id}'s stored gate "
                "verdict -- there is no diagnosis for this confirmation to answer."
            ],
        )

    stamped = {
        "way_id": way_id,
        # NOT truncated here: an over-long basis is refused by name below
        # (``confirmation_basis_too_long``), because a ledger that quotes half a
        # person's sentence quotes them saying something they did not say.
        "basis": str(basis or "").strip(),
        "confirmed_by": str(confirmed_by or "").strip(),
        "confirmed_at": _now_iso(),
        "acknowledged_collateral_way_ids": list(acknowledged_collateral_way_ids or []),
    }
    draft = job_draft_or_none(parent_job_id)
    targets = derive_fix_targets(
        finding_code,
        finding,
        draft=draft,
        params=None,
        job_id=parent_job_id,
        dead_end_confirmation=stamped,
    )
    offered = targets.get("dead_end_confirmation")
    derivation = targets.get("dead_end_derivation") or {}
    if offered is None or not offered.get("confirmed"):
        raise GenesisRefused(
            "genesis_dead_end_not_confirmable",
            [
                f"Job {parent_job_id}'s finding {finding_code!r} (subject "
                f"{finding['subject']!r}) does not accept a real-world dead-end confirmation: "
                f"{derivation.get('reason_code') or 'no_derivation'} -- "
                f"{derivation.get('detail') or 'nothing measured'}."
            ],
        )

    confirmed = dict(offered["confirmed"])
    excluded_way_id = int(confirmed["way_id"])

    # Every exclusion the PARENT already carried travels forward, and a repeat
    # refuses. Without this, a second confirmation on a sibling would silently
    # RE-INTRODUCE the way the first one's confirmer took responsibility for
    # removing -- an accountable human decision reversed by a path that never
    # mentions it, which is the exact class of silent difference this unit
    # exists to close. (Unreachable by re-confirming the same finding -- an
    # excluded way raises no finding -- and closed anyway, because "unreachable
    # today" is not a property this record's shape guarantees tomorrow.)
    inherited = [dict(entry) for entry in (parent.get("excluded_ways") or [])]
    if any(int(entry.get("way_id", -1)) == excluded_way_id for entry in inherited):
        raise GenesisRefused(
            "genesis_dead_end_already_excluded",
            [
                f"Way {excluded_way_id} is already excluded in job {parent_job_id} as an "
                "operator-confirmed real-world dead end, so this network does not contain it "
                "and there is nothing further to remove."
            ],
        )

    job_id = uuid4().hex[:12]
    frozen_osm = _freeze_parent_source(job_id, parent_source, recorded_sha)
    copied_sha = _sha256(frozen_osm)

    exclusion = {
        "way_id": excluded_way_id,
        "reason": SKIP_CONFIRMED_REAL_WORLD_DEAD_END,
        "finding_code": finding_code,
        "subject": finding["subject"],
        # The PARENT's link ids, and named as such: removing a way renumbers
        # every link after it (``draft``'s own link-table fingerprint note), so
        # these address the network the confirmer was LOOKING at and never the
        # one this job will emit. The stable id across both is the OSM way id.
        "parent_link_ids": list(offered.get("link_ids") or []),
        "node_id": offered.get("node_id"),
        "collateral_way_ids": list(offered.get("collateral_way_ids") or []),
        "basis": confirmed["basis"],
        "confirmed_by": confirmed["confirmed_by"],
        "confirmed_at": confirmed["confirmed_at"],
    }

    record = {
        "id": job_id,
        "workspace_id": workspace_id,
        "name": str(parent.get("name") or "regenerated"),
        "status": STATUS_QUEUED,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "submitted_by": confirmed["confirmed_by"],
        "options": dict(parent.get("options") or {}),
        "source_osm": {
            "filename": str(source_record.get("filename") or frozen_osm.name),
            "bytes": frozen_osm.stat().st_size,
            "sha256": copied_sha,
            "stored_as": frozen_osm.name,
        },
        "side_inputs": None,
        "network": None,
        "artifact": None,
        "layers": None,
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
        # THE declarative parameter, and the whole difference between this job
        # and its parent. Top-level rather than inside ``options`` on purpose:
        # ``options`` is the user's own generation settings, carried verbatim
        # from the parent, and burying a human's accountable claim inside them
        # would make the two indistinguishable to every reader of the record.
        # The parent's own exclusions lead, this confirmation appends -- a chain
        # of confirmations accumulates rather than replacing.
        "excluded_ways": [*inherited, exclusion],
        "derived_from": {
            "kind": DEAD_END_EXCLUSION_LINEAGE_KIND,
            "job_id": parent_job_id,
            "requested_by": confirmed["confirmed_by"],
            "requested_at": confirmed["confirmed_at"],
            "source_sha256": copied_sha,
            "parent_source_sha256": recorded_sha,
            "converter_fixes": [dict(entry) for entry in CONVERTER_FIXES],
            "findings": [
                {
                    "code": finding_code,
                    "subject": finding["subject"],
                    "derivation_reason_code": "source_shares_the_gap",
                }
            ],
            "exclusions": [*inherited, exclusion],
            "confirmed": exclusion,
            # The derivation that PRODUCED the exclusion, verbatim -- so the
            # lineage names not just what was removed but what was measured
            # before a person was asked, including the collateral they had to
            # acknowledge.
            "dead_end_derivation": dict(derivation),
            "reason": (
                f"{finding_code} / {finding['subject']}: operator-confirmed real-world dead end"
            ),
        },
    }
    _upsert(record)
    return record


def start_dead_end_exclusion_job(parent_job_id: str, **kwargs: Any) -> dict[str, Any]:
    """:func:`create_dead_end_exclusion_job`, then run it on a daemon worker
    thread through :func:`run_genesis_job` — the SAME runner every other
    generation uses, for the same reason unit #38 states."""
    record = create_dead_end_exclusion_job(parent_job_id, **kwargs)
    threading.Thread(
        target=run_genesis_job,
        args=(record["id"], record["workspace_id"]),
        daemon=True,
    ).start()
    return record


# ---------------------------------------------------------------------------
# Rendering the verdict for the UI (ko primary, en/ja through the gate's own table)
# ---------------------------------------------------------------------------


def _gate_summary(
    result: GateResult,
    *,
    live_oracle: LoadOracle | None,
    live_oracle_gap: str | None,
    attestation: dict[str, Any] | None = None,
    regate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The compact verdict carried on the job record (the full result is on disk).

    ``live_evidence`` states what was offered and whether the gate ADMITTED it,
    read back off V1's own evidence rather than asserted here — so a UI can say
    "a live check was attached but it names another network" instead of showing
    a check that mysteriously stayed grey.

    It also states the BASIS, and the basis is the ATTESTATION's own — never a
    constant assumed here. Evidence that arrived over HTTP is an operator's
    attestation, not something this app proved (module docstring), so it carries
    ``basis: "operator_attested"`` and the attester's name; evidence a live-check
    JOB produced by opening the file itself carries
    :data:`LIVE_CHECK_BASIS` instead (unit #33). A caller that supplies no
    attestation at all reads as the former, which is the conservative default: a
    verdict may understate what it rests on, never overstate it. A reader must
    never have to infer which kind of V1 they are looking at.
    """
    v1 = result.check("V1")
    basis = str((attestation or {}).get("basis") or ATTESTATION_BASIS)
    evidence: dict[str, Any] | None = None
    if live_oracle is not None:
        admitted = v1.state is not CheckState.NOT_CHECKABLE
        evidence = {
            "label": live_oracle.label,
            "network": Path(live_oracle.network_path).name,
            "vissim_version": live_oracle.vissim_version,
            "sha256": live_oracle.network_sha256,
            "admitted": admitted,
            "gap_codes": [gap.code for gap in v1.gaps],
            "basis": basis,
            "attested_by": (attestation or {}).get("by"),
            "attested_at": (attestation or {}).get("at"),
        }
    return {
        "verdict": result.verdict.value,
        "counts": result.counts(),
        "blocking": [check.id for check in result.blocking],
        "checked_at": _now_iso(),
        "network_sha256": result.network_sha256,
        # V5's and V6's coverage clauses change TIER on this, so ``counts`` means
        # two different things depending on it. A reader of the compact summary
        # must not have to fetch the full ``gate.json`` and recognise a gap code
        # to discover that a refuse-tier finding was reported as a warning.
        "provenance": result.provenance.as_dict(),
        "live_evidence": evidence,
        "live_evidence_gap": None if live_oracle is not None else live_oracle_gap,
        # True only when a refuse-tier check went green on a human's claim
        # rather than on something this process observed. The UI reads this to
        # avoid presenting an attested pass as a proven one. A live-check JOB's
        # own evidence is the case where this is legitimately False WITH a green
        # V1: the app opened the file itself, so there is no claim to rest on
        # (unit #33) -- and the UI still says which basis it was, because
        # "not an attestation" is not the same sentence as "proven".
        "rests_on_attestation": bool(
            live_oracle is not None
            and v1.state is CheckState.PASS
            and basis == ATTESTATION_BASIS
        ),
        # What caused THIS verdict to replace an earlier one, and where the
        # earlier one's bytes were kept. ``None`` on a first gate.
        "regate": regate,
    }


def gate_payload(job_id: str, language: str = "ko") -> dict[str, Any]:
    """The stored gate result, rendered check by check for a human.

    Reads ``gate.json`` from the job directory rather than re-running the gate:
    the verdict a user is looking at must be the verdict that decided whether
    the artifact was published, not a fresh one that could differ.

    Every string comes from ``gate.py``'s own ko/en/ja tables (``check.title``,
    ``finding.message``, ``gap.message``, ``render_report``) — this unit renders
    the gate's words, it does not author replacements for them.
    """
    record = get_job(job_id)
    work_dir = job_dir(job_id)
    stored = work_dir / GATE_JSON_FILENAME
    if not stored.is_file():
        raise GenesisJobNotFoundError(f"Job {job_id} has no stored gate result.")
    raw = json.loads(stored.read_text(encoding="utf-8"))
    result = GateResult.model_validate(_gate_result_kwargs(raw))

    checks: list[dict[str, Any]] = []
    for check in result.checks:
        findings = check.findings[:MAX_FINDINGS_IN_PAYLOAD]
        warnings = check.warnings[:MAX_FINDINGS_IN_PAYLOAD]
        checks.append(
            {
                "id": check.id,
                "tier": check.tier.value,
                "state": check.state.value,
                "title": check.title(language),
                "blocks": check.blocks,
                "findings": [
                    {"code": f.code, "subject": f.subject, "message": f.message(language)}
                    for f in findings
                ],
                "findings_omitted": max(len(check.findings) - len(findings), 0),
                "warnings": [
                    {"code": f.code, "subject": f.subject, "message": f.message(language)}
                    for f in warnings
                ],
                "warnings_omitted": max(len(check.warnings) - len(warnings), 0),
                "gaps": [{"code": g.code, "message": g.message(language)} for g in check.gaps],
                "evidence": check.evidence,
            }
        )

    return {
        "job_id": job_id,
        "language": language,
        "network": result.network,
        "network_sha256": result.network_sha256,
        "verdict": result.verdict.value,
        "counts": result.counts(),
        "provenance": result.provenance.as_dict(),
        "status": record.get("status"),
        "publication": record.get("publication"),
        "revocation": record.get("revocation"),
        # ``None`` for a geometry-only generation. Carried here so a reader of
        # "V6 passed" can see WHAT passed (four vehicle inputs, one composition)
        # without a second request — and so a NOT_CHECKABLE V5 beside an absent
        # signal layer reads as the one fact it is.
        "layers": record.get("layers"),
        "live_evidence": (record.get("gate") or {}).get("live_evidence"),
        "checks": checks,
        "report_markdown": render_report(result, language=language),
    }


def _gate_result_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    """``GateResult.as_dict()`` back into constructor shape.

    ``as_dict`` adds a derived ``counts`` block and the model forbids nothing
    else, so the round trip is a key drop plus the nested check models pydantic
    validates itself.
    """
    kwargs: dict[str, Any] = {
        "network": raw.get("network", ""),
        "network_sha256": raw.get("network_sha256", ""),
        "verdict": raw.get("verdict"),
        "checks": raw.get("checks", []),
    }
    # Carried across the round trip because ``render_report`` prints it and V5's
    # and V6's tier depends on it: dropping it would make a stored verdict read
    # back as "provenance unproven" for a file the gate proved was ours. Absent
    # in a ``gate.json`` written before 2026-08-11, and the model's default —
    # UNKNOWN — is the right answer for one of those.
    if isinstance(raw.get("provenance"), dict):
        kwargs["provenance"] = raw["provenance"]
    return kwargs


def artifact_path(job_id: str, kind: str) -> Path:
    """One of this job's artifact files, by KIND rather than by client-supplied
    name — a client never names a file this function opens.

    ``kind`` is one of ``inpx``, ``genesis_json``, ``compliance_md``,
    ``gate_json``, ``gate_report``, ``layers_json``, ``source_osm``.

    ``layers_json`` resolves by FIXED name rather than through the artifact
    record, like the two gate files and unlike the ``<name>.*`` ones, because it
    has to be reachable for a generation that produced no artifact at all — a
    side input that refused is exactly the case where a caller most needs the
    findings.
    """
    record = get_job(job_id)
    work_dir = job_dir(job_id)
    if kind == "gate_json":
        candidate = work_dir / GATE_JSON_FILENAME
    elif kind == "gate_report":
        candidate = work_dir / GATE_REPORT_FILENAME
    elif kind == "layers_json":
        candidate = work_dir / LAYERS_JSON_FILENAME
    elif kind == "source_osm":
        # Mirrors ``_run_genesis_job_locked``'s own resolution of this file —
        # the record never carries a server path (see the module-level note by
        # ``create_genesis_job``), so ``stored_as`` re-derived under
        # ``job_dir`` is the only way back to it, on the runner's side and here.
        source = record.get("source_osm") or {}
        stored_as = source.get("stored_as")
        if not stored_as:
            raise GenesisJobNotFoundError(f"Job {job_id} has no {kind!r} artifact.")
        candidate = work_dir / str(stored_as)
    else:
        artifact = record.get("artifact") or {}
        filename = artifact.get(kind)
        if not filename:
            raise GenesisJobNotFoundError(f"Job {job_id} has no {kind!r} artifact.")
        candidate = work_dir / str(filename)
    if not candidate.is_file():
        raise GenesisJobNotFoundError(f"Job {job_id} has no {kind!r} artifact on disk.")
    return candidate


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _resolve_name(
    name: str | None, osm_path: Path, source_filename: str | None
) -> str:
    """The artifact base name: the caller's, else the upload's stem, sanitized.

    Validated rather than escaped when the caller supplied one — silently
    rewriting somebody's requested name produces files they cannot find.
    """
    if name is not None:
        cleaned = name.strip()
        if not _NAME_RE.match(cleaned):
            raise GenesisRefused(
                "genesis_bad_name",
                [
                    f"{name!r} is not a usable network name. Use 1–64 characters from "
                    "A–Z, a–z, 0–9, '.', '_' and '-', starting with a letter or digit."
                ],
            )
        return cleaned
    stem = Path(source_filename or osm_path.name).stem
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", stem).lstrip("._-")[:64]
    return sanitized or "genesis_draft"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "reconcile_interrupted_jobs",
    "COUNTS_DOC_FILENAME",
    "COUNTS_KIND",
    "GATED_STATUSES",
    "GATE_HISTORY_DIRNAME",
    "GATE_JSON_FILENAME",
    "GATE_REPORT_FILENAME",
    "LAYERS_JSON_FILENAME",
    "LIVE_CHECK_BASIS",
    "LIVE_CHECK_LINEAGE_KIND",
    "LIVE_CHECK_STATE_FILENAME",
    "GenesisJobError",
    "GenesisJobNotFoundError",
    "GenesisRefused",
    "GenesisRegistryError",
    "MAX_FINDINGS_IN_PAYLOAD",
    "STATUS_CHECKED",
    "STATUS_ERROR",
    "STATUS_PROVISIONAL",
    "STATUS_PUBLISHED",
    "STATUS_QUEUED",
    "STATUS_REFUSED",
    "STATUS_RUNNING",
    "StagedOsmNotFoundError",
    "TERMINAL_STATUSES",
    "PHASE_APPLYING_FIX",
    "PHASE_LIVE_CHECKING",
    "artifact_path",
    "attach_live_check",
    "create_dead_end_exclusion_job",
    "create_genesis_fix_job",
    "create_genesis_job",
    "create_live_check_job",
    "start_dead_end_exclusion_job",
    "DEAD_END_BASIS_MAX_CHARS",
    "DEAD_END_EXCLUSION_LINEAGE_KIND",
    "V3_CONNECTIVITY_FINDING_CODES",
    "V7_GEOMETRY_FINDING_CODES",
    "derive_fix_targets",
    "derive_v3_dead_end_confirmation",
    "derive_v3_source_testimony",
    "derive_v6_node_evidence",
    "derive_v7_corrected_polyline",
    "discard_staged_osm",
    "discard_staged_side_input",
    "gate_and_publish",
    "gate_payload",
    "genesis_dir",
    "get_job",
    "iter_stored_gate_findings",
    "job_dir",
    "job_draft_or_none",
    "list_jobs",
    "list_staged_side_inputs",
    "registry_path",
    "run_genesis_fix_job",
    "run_genesis_job",
    "run_live_check_job",
    "side_staging_dir",
    "stage_counts",
    "stage_osm",
    "stage_side_input",
    "staged_counts",
    "staged_osm_path",
    "staged_side_input",
    "staging_dir",
    "start_genesis_fix_job",
    "start_genesis_job",
    "start_live_check_job",
]

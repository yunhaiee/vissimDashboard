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
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping
from uuid import uuid4

from services import datasets, observations, workspaces
from services.dataimport.adapters.daejeon import DaejeonCountAdapter
from services.dataimport.base import AdapterError, ImportResult, IssueSeverity
from services.genesis import side_inputs as side_inputs_module
from services.genesis.compose import NO_LAYERS, ComposedLayers
from services.genesis.draft import DraftNetwork, build_draft
from services.genesis.emit import EmitRefused, emit_inpx
from services.genesis.gate import (
    CheckState,
    GateResult,
    GateVerdict,
    LoadOracle,
    render_report,
    run_gate,
)
from services.genesis.osm import (
    parse_osm_file,
    requested_bbox_from_provenance,
    requested_polygon_from_provenance,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, see _build_fix_edit_session
    from services.agent.edit_session import VissimEditSession

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


def derive_fix_targets(
    code: str,
    finding: Mapping[str, Any],
    *,
    draft: "DraftNetwork | None" = None,
    params: Mapping[str, Any] | None = None,
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
    * **V6** (``proposed_volume``) / **V7** (``corrected_points_xy``) — the
      ask-first fields. Threaded from ``params`` ONLY (never derived here):
      these are exactly the values the module docstring by
      ``genesis_fixes._build_v6_entrance_volume`` names as the APPROVER's
      alone to supply.
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

    if code.startswith("v7_") and params is not None and params.get("points_xy") is not None:
        targets["corrected_points_xy"] = params.get("points_xy")

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
    approved_at, parent_sha256}``, present ONLY on a fix job's record — an
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
    targets = derive_fix_targets(finding_code, finding, draft=draft, params=params)
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
    "create_genesis_fix_job",
    "create_genesis_job",
    "create_live_check_job",
    "derive_fix_targets",
    "derive_v6_node_evidence",
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

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
    Every refuse-tier check genuinely passed, V1 included. Reachable only after
    :func:`attach_live_check` has fed a real ``LoadNet``'s evidence back in.

**No COM here, and that is why ``provisional`` exists.** A generation job runs
in a worker thread inside the API process; ``docs/phase2/00-EXECUTION-GUIDE.md``
§1 and this project's CLAUDE.md both forbid COM outside the dedicated lane, so
this module never imports ``services.vissim_com`` and never shells out to a
probe. V1's evidence arrives the same way ``tools/genesis_validation_gate.py``
takes it — as ``tools/genesis_open_live_check.py``'s ``state.json`` — through
:func:`attach_live_check`, and ``gate.py``'s sha256 identity rule decides
whether it is evidence about THIS file.

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
from typing import Any
from uuid import uuid4

from services import datasets, workspaces
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

TERMINAL_STATUSES = frozenset(
    {STATUS_REFUSED, STATUS_PROVISIONAL, STATUS_PUBLISHED, STATUS_ERROR}
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

#: What a V1 that went green on ATTACHED evidence actually rests on. Carried on
#: the job record, in the gate summary and into the UI, so a verdict resting on
#: a human's claim never reads like one the app proved for itself. The app
#: cannot prove Vissim ran from a JSON a client uploaded (module docstring), and
#: naming the basis is how that stays visible instead of becoming folklore.
ATTESTATION_BASIS = "operator_attested"

#: Progress the UI shows per phase. Coarse and honest: these are the real step
#: boundaries this runner has, and there is no way to sample inside
#: ``build_draft`` without perturbing it, so nothing finer is invented.
_PHASE_PROGRESS = {
    PHASE_QUEUED: 0.0,
    PHASE_READING_OSM: 0.1,
    PHASE_DRAFTING: 0.35,
    PHASE_EMITTING: 0.7,
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
    # Function-local: ``services.osm_fetch`` imports THIS module at module
    # level (it stages fetched extracts through ``stage_osm``), so a top-level
    # import here would be a cycle.
    from services.osm_fetch import read_provenance  # noqa: PLC0415

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
    from services.osm_fetch import read_provenance  # noqa: PLC0415

    try:
        provenance = read_provenance(osm_path.read_bytes())
    except OSError:  # pragma: no cover - the parse below reports the same file
        return None, None
    return (
        requested_bbox_from_provenance(provenance),
        requested_polygon_from_provenance(provenance),
    )


def _run_genesis_job_locked(job_id: str) -> dict[str, Any]:
    record = get_job(job_id)
    try:
        _update(job_id, status=STATUS_RUNNING, phase=PHASE_READING_OSM)
        # Re-derived from the job id rather than read off the record: the
        # record crosses the HTTP boundary and must not carry server paths.
        osm_path = job_dir(job_id) / str(record["source_osm"]["stored_as"])
        exclude_service = bool((record.get("options") or {}).get("exclude_service"))

        requested_bbox, requested_polygon = _requested_region_of(osm_path)
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


def gate_and_publish(
    job_id: str,
    *,
    live_oracle: LoadOracle | None = None,
    live_oracle_gap: str | None = None,
    attestation: dict[str, Any] | None = None,
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
    because the app watched Vissim or because somebody said it did.
    """
    record = get_job(job_id)
    artifact = record.get("artifact")
    if not artifact:
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
        publication = _register_as_active_dataset(record, inpx_path)
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


def _register_as_active_dataset(record: dict[str, Any], inpx_path: Path) -> dict[str, Any]:
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
    """
    previous = datasets.get_active_dataset()
    stored = datasets.save_active_dataset(
        f"{record['name']}.inpx", inpx_path.read_bytes()
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
) -> dict[str, Any]:
    """The compact verdict carried on the job record (the full result is on disk).

    ``live_evidence`` states what was offered and whether the gate ADMITTED it,
    read back off V1's own evidence rather than asserted here — so a UI can say
    "a live check was attached but it names another network" instead of showing
    a check that mysteriously stayed grey.

    It also states the BASIS. Evidence that arrived over HTTP is an operator's
    attestation, not something this app proved (module docstring), so it carries
    ``basis: "operator_attested"`` and the attester's name. A reader must never
    have to infer which kind of V1 they are looking at.
    """
    v1 = result.check("V1")
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
            "basis": ATTESTATION_BASIS,
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
        # avoid presenting an attested pass as a proven one.
        "rests_on_attestation": bool(
            live_oracle is not None and v1.state is CheckState.PASS
        ),
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
        source = record.get("source_osm") or {}
        candidate = Path(str(source.get("path") or ""))
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
    "GATE_JSON_FILENAME",
    "GATE_REPORT_FILENAME",
    "LAYERS_JSON_FILENAME",
    "GenesisJobError",
    "GenesisJobNotFoundError",
    "GenesisRefused",
    "GenesisRegistryError",
    "MAX_FINDINGS_IN_PAYLOAD",
    "STATUS_ERROR",
    "STATUS_PROVISIONAL",
    "STATUS_PUBLISHED",
    "STATUS_QUEUED",
    "STATUS_REFUSED",
    "STATUS_RUNNING",
    "StagedOsmNotFoundError",
    "TERMINAL_STATUSES",
    "artifact_path",
    "attach_live_check",
    "create_genesis_job",
    "discard_staged_osm",
    "discard_staged_side_input",
    "gate_and_publish",
    "gate_payload",
    "genesis_dir",
    "get_job",
    "job_dir",
    "list_jobs",
    "list_staged_side_inputs",
    "registry_path",
    "run_genesis_job",
    "side_staging_dir",
    "stage_counts",
    "stage_osm",
    "stage_side_input",
    "staged_counts",
    "staged_osm_path",
    "staged_side_input",
    "staging_dir",
    "start_genesis_job",
]

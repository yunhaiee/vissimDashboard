"""``load_check``: v2 unit (3)'s empirically-grounded answer to "expose
Vissim's own network-consistency checking to the agent".

**Read this before touching this file — the design here is NOT what the
roadmap originally asked for; it is what Phase 1's live probe
(``backend/tools/check_network_probe.py``) proved actually exists.**

Phase 1 findings (paste of the live run lives in the v2 unit (3) report;
summarized here because they are the reason this module looks the way it
does):

1. **No COM network-consistency-check surface exists at all.** A static
   scan of every method name across the ENTIRE installed "Vissim Object
   Library 26.0" type library (996 COM interfaces, ~90k lines of generated
   makepy source) for anything containing check/consist/valid/verify/
   diagnos/integrity/sanity/repair matched ZERO methods. A live,
   gencache-early-bound ``dir()`` of the connected ``Vissim``/``Vissim.Net``/
   ``Vissim.Simulation`` objects confirms the same (zero keyword matches;
   ``IVissim``'s full method list is exactly
   ApplyModelTransferFile/ApplyPreconfiguredDrivingStyle/
   ApplyPreconfiguredWeather/AttValue/BringToFront/Exit/
   ExportToModelArchive/GenerateModelTransferFile(BetweenFiles)/
   ImportANM/ImportCoordinateRoutes/ImportOpenDrive/ImportResults/
   LoadLayout/LoadNet/LoadProject/Log/New/PlaceUnderScenarioManagement/
   ResumeUpdateGUI/SaveLayout/SaveNet/SaveNetAs/SetAttValue/
   SetResultsFolder/SuspendUpdateGUI/VissimTestCmd — no "check" verb among
   them; ``INet`` exposes only its object-collection properties plus
   AttValue/SetAttValue). A battery of plausible AttValue ids
   (``IsConsistent``/``IsValid``/``NetworkValid``/``ErrorCount``/
   ``Messages``/... ) either raised "Could not find attribute with ID X"
   (Vissim/Simulation objects) or returned ``None`` for EVERY candidate
   INCLUDING a deliberately made-up control id (``Net`` object) — i.e.
   ``Net.AttValue`` fails OPEN (silently returns ``None`` for any unknown
   id) rather than raising, so it can never be trusted as an existence
   probe on that object. This rules out branch (a) of the task brief
   entirely: there is no genuine COM check verb to wrap.

2. **LoadNet-of-a-deliberately-broken-clone IS diagnosable, but the shape
   depends on WHICH reference is broken, and it is never a full
   consistency check:**

   - Deleting the network's only ``<signalController>`` (referenced by
     ALL 52 ``<signalHead>`` elements via ``sg="<sc_no> <group>"`` on the
     Beijing example) makes ``LoadNet`` itself RAISE: a generic
     ``com_error`` — "VISSIM.Vissim.2600: The network file could not be
     loaded." No object identity, no field name, no .err file at all (the
     failure happens before Vissim gets far enough to write one). Genuine
     refusal, but a low-information one.
   - Deleting a ``<link>`` referenced only by a ``<signalHead>``'s
     ``lane="<link_no> <lane_idx>"`` attribute (this specific corruption
     happened to also break several Static Vehicle Routes that traversed
     the link) makes ``LoadNet`` SUCCEED silently — the network loads,
     ``Net.SignalControllers``/``Net.VehicleInputs`` look completely
     normal, no .err file appears yet. The corruption only surfaces when a
     simulation is actually started: ``Simulation.RunSingleStep()`` (ONE
     step — confirmed empirically to raise in ~0.3s, not requiring a real
     duration) raises "The simulation could not be continued." AND writes
     a genuinely structured, diagnosable ``<inpx-stem>.err`` file:
     ``Error\tStatic Vehicle Route "4 - 2" is not complete.\tNetwork
     object type: Static Vehicle Route\tNetwork object keys: 4-2`` (one
     line per broken route) followed by ``Error\tThe simulation run could
     not be initialized due to errors.``. ``RunContinuous`` for a full 60s
     produces the IDENTICAL failure/.err content at ~200x the cost — this
     is why :func:`_load_check` uses ``RunSingleStep`` once, never a real
     duration.

   **Conclusion: branch (b) of the task brief** — "the closest verifiable
   subset" is a load-and-minimal-initialize check: attempt ``LoadNet``
   then ONE ``RunSingleStep`` on the session's current working copy
   (NEVER a real run — no evaluation is configured, no duration/seed
   chosen, no result database is ever meant to materialize), report
   whichever of the two stages failed (if either) with Vissim's own
   exception text, and surface any NEW ``.err`` content the attempt
   produced. This is explicitly, structurally NOT a full network
   consistency check (see ``load_check``'s own docstring and the ko/en/ja
   prose in ``prompts/v1_system.md`` §5) — it only catches the SPECIFIC
   failure classes Vissim's own ``LoadNet``/simulation-initialization
   already happen to refuse on, which is a strict subset of "the network
   is semantically correct" (e.g. a corruption that breaks nothing
   Vissim's loader/initializer itself checks — most of the semantic
   landmines in 00-EXECUTION-GUIDE.md §3.2, like a passive conflict area
   or an intergreen violation — would NOT be caught by this tool at all;
   it would report ``load_ok: true, init_ok: true`` on a network that is
   still perfectly capable of lying about its results once actually run).

**Why tier="run", never a free-dispatch read tool.** Every attempt costs a
real, dedicated Vissim instance (``attach_to_running=False`` per
00-EXECUTION-GUIDE.md rule 3) and a licence seat — identical cost profile
to :func:`services.agent.tools_run.run_simulation`. The same
approval/gate machinery therefore applies (``orchestrator.py``'s
``_GATED_TIERS``), even though this tool never mutates the network and
has no ``postwrite``-shaped verification: its own returned findings ARE
the verification (a read of Vissim's load/init behavior, nothing more) —
mirrors the task brief's branch-(a) framing of "verification = the
findings themselves ... no mutation — no postwrite", carried over to
this branch since it is equally true here.

**Deliberately NOT shaped like a ``run_result``.** The returned dict's
diagnostic payload is named ``load_check_result``, never ``run_result`` —
``orchestrator.py`` reads ``(envelope.get("data") or {}).get("run_result")``
and feeds it to ``GuardrailEngine.postrun``, whose registered postrun
constraints (``results_missing_kpi_tables``, ``vehicles_deleted_in_run``,
``error_log_overwritten_between_runs``) all assume a REAL run's
``{tables, deleted_vehicles, evaluation_configured}`` shape. If this
tool's output happened to be named ``run_result`` with a different shape,
those constraints would either crash on a missing key or (worse) silently
misinterpret a diagnostic probe as a real completed run. Naming it
differently makes every postrun constraint's ``run_result is None`` guard
degrade silently and correctly — verified by
``test_load_check_postrun_finds_nothing_to_interpret`` in
``tests/test_agent_tools_check.py``.

**Reuses ``tools_run``'s active-run-source, does not mirror it.** Unlike
``tools_run.py`` choosing NOT to reuse ``tools_network.set_active_inpx``
(a distinct concept — "which network is being read" vs "which network
gets submitted to the job queue" — see that module's own docstring and
docs/phase2/DECISIONS.md), this module's need is IDENTICAL to
``tools_run``'s: "which working copy does this run-tier action for THIS
turn operate against". ``routers/assistant.py`` already calls
``tools_run.set_active_run_source(artifact_manager.working_path)``
unconditionally for every turn (not gated on whether a write/edit session
is needed), so reusing the PUBLIC ``tools_run.get_active_run_source()``
accessor here needs zero new router wiring and avoids a third redundant
module-global for the exact same value. This mirrors ``tools_edit.py``
importing ``edit_session.get_active_edit_session`` — a tool module
consuming a sibling module's PUBLIC accessor, not reaching into a private
symbol (the pattern this codebase reserves "mirror instead of reach in"
for is specifically about PRIVATE, underscore-prefixed state).

**Runs directly against the turn's ``working.inpx`` — no extra clone.**
``ArtifactManager.clone()`` already copies the SOURCE dataset into
``work_dir/working.inpx`` as a single, self-contained file (Vissim 2026's
.inpx format is self-contained; no sidecar files are required for
``LoadNet`` — the production ``VissimEditSession``/job-queue paths already
prove this against real Vissim). The one accepted side effect: a failed
``RunSingleStep`` attempt makes Vissim write a stray ``working.err`` next
to ``working.inpx`` in the session's ``work_dir`` — never colliding with
anything (real ``run_simulation`` jobs clone onto a COMPLETELY separate
``runtime/simulations/<job_id>/seed_NN/`` path, never the session's own
work dir) and harmless to every existing ``work_dir`` consumer
(``ArtifactManager._max_existing_version()`` only matches
``edit_NNN.inpx``). Left on disk deliberately, not cleaned up — it is the
same class of diagnostic residue a real run already leaves behind
elsewhere in this system.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.agent import tools_run
from services.agent.toolkit import tool

logger = logging.getLogger(__name__)

#: Own, small concurrency cap — this tool launches a real Vissim instance
#: just like an edit session or a run job, so it counts against the same
#: ~4-instances-per-machine ceiling (00-EXECUTION-GUIDE.md §3.1). Kept at 1
#: (smaller than edit_session.py's 2): this is a cheap, short diagnostic
#: probe, not something a session legitimately needs to run several of at
#: once. Mirrors edit_session.py's acquire-with-timeout pattern exactly
#: (own semaphore, not a shared one — same "keeps this module
#: self-contained and independently testable" rationale as tools_run.py's
#: choice not to reuse tools_network's active-inpx module-global).
MAX_CONCURRENT_LOAD_CHECKS = 1
_SEMAPHORE_ACQUIRE_TIMEOUT_S = 90.0
_load_check_semaphore = threading.Semaphore(MAX_CONCURRENT_LOAD_CHECKS)

#: Cap on how much raw .err text rides in the tool envelope — an .err file
#: with hundreds of broken-route lines is realistic (every route through a
#: deleted link fails) and must not flood the planner's context. Mirrors
#: toolkit.py's _MAX_EXCEPTION_SUMMARY_LEN discipline (a length cap, not a
#: line cap, so partial-line truncation is possible but always marked).
_MAX_ERR_CONTENT_CHARS = 4000


class NoActiveCheckSourceError(RuntimeError):
    """No active run source for this turn (mirrors
    ``tools_run.NoActiveRunSourceError``'s rationale exactly: raised, never
    returned as a dict, so ``toolkit.dispatch`` reports a genuine
    ``status: "error"`` envelope instead of a false ``"ok"``). A distinct
    exception TYPE from ``tools_run``'s (rather than re-raising that one)
    keeps this module's own error handling self-contained even though the
    underlying VALUE comes from ``tools_run.get_active_run_source()``."""


def _require_active_source() -> Path:
    source = tools_run.get_active_run_source()
    if source is None:
        raise NoActiveCheckSourceError(
            "No active network source for this turn (error_type: no_active_check_source). "
            "load_check only runs during an agent turn with a working copy set up."
        )
    if not source.exists():
        raise FileNotFoundError(f"Active check source does not exist: {source}")
    return source


def _summarize_exception(exc: Exception) -> str:
    """One-line, un-truncated-unless-huge rendering of a raised exception —
    ``com_error`` tuples in particular are already fairly compact; this
    just normalizes to ``str(exc)`` with the class name prefixed so a
    generic ``com_error`` is distinguishable from e.g. a ``FileNotFoundError``."""
    return f"{type(exc).__name__}: {exc}"


def _snapshot_err_paths(directory: Path) -> set[Path]:
    try:
        return set(directory.glob("*.err"))
    except OSError:
        return set()


def _read_new_err_content(directory: Path, pre_existing: set[Path]) -> str | None:
    """Content of any ``*.err`` file that appeared in ``directory`` since
    ``pre_existing`` was snapshotted — ``None`` when nothing new appeared
    (the common case: LoadNet itself never wrote an .err in EITHER Phase 1
    corruption mode; only a failed RunSingleStep did). Concatenates every
    new file (there is normally at most one) and caps total length."""
    try:
        new_paths = sorted(p for p in directory.glob("*.err") if p not in pre_existing)
    except OSError:
        return None
    if not new_paths:
        return None
    chunks: list[str] = []
    for path in new_paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            chunks.append(f"[could not read {path.name}: {exc}]")
    content = "\n".join(chunks).strip()
    if len(content) > _MAX_ERR_CONTENT_CHARS:
        content = content[:_MAX_ERR_CONTENT_CHARS].rstrip() + "\n... [truncated]"
    return content or None


def _load_check(inpx_path: Path) -> dict[str, Any]:
    """The actual COM work: fresh connect -> LoadNet -> (if that succeeded)
    ONE RunSingleStep -> disconnect. NEVER calls save_net_as/persist —
    ``inpx_path`` is only ever read, never written by this function itself
    (Vissim's own LoadNet/RunSingleStep may write a sibling ``.err``/``.db``
    as a side effect — see the module docstring).

    A thin, separately-named seam (mirrors ``tools_run._run_job_and_wait``)
    so tests can ``monkeypatch.setattr(tools_check, "_load_check", ...)``
    with a fake outcome — no real Vissim COM, no real thread, no real
    filesystem I/O — while the real COM sequencing here is exercised only
    by the live acceptance script
    (``backend/tools/load_check_live_check.py``).

    Returns a plain dict; does NOT raise for an EXPECTED Vissim-side
    refusal (LoadNet or RunSingleStep failing IS the informative result
    this tool exists to report, not a ``tool_execution_error``) — only a
    genuine infrastructure failure (COM connect itself failing, e.g. no
    Vissim instance available at all) propagates as a raised exception,
    exactly like ``run_simulation``'s own COM-adjacent failure modes.
    """
    import pythoncom

    pythoncom.CoInitialize()
    try:
        from services.vissim_com import VissimController

        controller = VissimController()
        try:
            controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)

            pre_existing_err = _snapshot_err_paths(inpx_path.parent)

            try:
                controller.load_network(str(inpx_path))
            except Exception as exc:
                return {
                    "load_ok": False,
                    "load_error": _summarize_exception(exc),
                    "init_ok": None,
                    "init_error": None,
                    "err_content": _read_new_err_content(inpx_path.parent, pre_existing_err),
                }

            try:
                controller.run_single_step()
            except Exception as exc:
                return {
                    "load_ok": True,
                    "load_error": None,
                    "init_ok": False,
                    "init_error": _summarize_exception(exc),
                    "err_content": _read_new_err_content(inpx_path.parent, pre_existing_err),
                }

            return {
                "load_ok": True,
                "load_error": None,
                "init_ok": True,
                "init_error": None,
                "err_content": _read_new_err_content(inpx_path.parent, pre_existing_err),
            }
        finally:
            controller.disconnect()
    finally:
        pythoncom.CoUninitialize()


def _run_load_check_with_slot(inpx_path: Path) -> dict[str, Any]:
    acquired = _load_check_semaphore.acquire(timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S)
    if not acquired:
        raise RuntimeError(
            f"Could not start a Vissim load_check: {MAX_CONCURRENT_LOAD_CHECKS} concurrent "
            "load_check(s) are already running on this machine. Try again shortly."
        )
    try:
        return _load_check(inpx_path)
    finally:
        _load_check_semaphore.release()


# ---------------------------------------------------------------------------
# load_check() — zero params, operates on the turn's active working copy
# ---------------------------------------------------------------------------


class LoadCheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="load_check", tier="run", provider="check", params=LoadCheckParams)
def load_check(params: LoadCheckParams) -> dict[str, Any]:
    """Attempt to load the current network in Vissim and take one simulation step, reporting whether Vissim itself accepts it as loadable and initializable -- NOT a full network consistency check (Vissim's COM API exposes no such capability; this only catches load-time and initialization-time refusals, e.g. a dangling object reference)."""
    del params
    source_inpx = _require_active_source()
    result = _run_load_check_with_slot(source_inpx)

    if not result["load_ok"]:
        message = (
            "Vissim refused to load the current network -- it is not in a loadable state. "
            f"Vissim's error: {result['load_error']}"
        )
    elif not result["init_ok"]:
        message = (
            "The network loaded, but Vissim could not initialize a simulation run on it "
            f"(a structural problem the loader itself did not catch). Vissim's error: {result['init_error']}"
        )
    else:
        message = (
            "The network loaded and a minimal simulation initialization step succeeded -- "
            "no load-time or initialization-time errors were found. This does NOT mean the "
            "network is fully consistent: Vissim's COM API has no general consistency-check "
            "capability, so semantic issues (e.g. passive conflict areas, intergreen "
            "violations) are not covered by this check."
        )

    return {
        "message": message,
        "verification": {"load_ok": result["load_ok"], "init_ok": result["init_ok"]},
        # Deliberately NOT named "run_result" -- see module docstring on why
        # postrun constraints must never interpret this as a real run.
        "load_check_result": result,
    }


__all__ = [
    "LoadCheckParams",
    "MAX_CONCURRENT_LOAD_CHECKS",
    "NoActiveCheckSourceError",
    "load_check",
]

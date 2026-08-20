"""Unit #51 (live runner) — the ONE candidate → ONE scored run implementation.

What this closes
----------------
``parameter_search.SearchRunner`` has been a Protocol with two implementations
since #51: ``SyntheticRunner`` (a known GEH surface, for tests) and nothing at
all for a real network. ``parameter_search.runner_requirement()`` said so
outright — ``wired: False``, with a five-line ``requires`` list. This module is
that list, executed:

===========================================================  ==================
``runner_requirement()['requires']``                          where it happens
===========================================================  ==================
clone the active dataset into a job work dir, never in place  :meth:`VissimSearchRunner._candidate_copy`
apply the vector with ``set_driving_behavior_parameter``,     :meth:`VissimSearchRunner._apply`
one field at a time
run through the job queue with RunContinuous, pinned seed     :meth:`VissimSearchRunner.run`
(the job is ``services.agent.search_job``; this class is    (+ :meth:`_pin_single_run`,
only ever called from that job's thread)                     :meth:`_run_completed`)
score with ``build_reality_check_report`` against the SAME    :meth:`VissimSearchRunner._score`
observation set and period the preconditions used
return ``validation.geh`` per node — None, never 0.0          :func:`geh_by_node`
===========================================================  ==================

WHY THIS IS THIN, AND WHAT COVERS IT
------------------------------------
Every COM line here is a line ``routers/simulations.py``'s
``_run_agent_simulation_job_locked`` already runs in production, in the same
order, with the same fail-closed checks: ``connect(visible=False,
attach_to_running=False)`` (never ``GetActiveObject`` — it attaches to a human's
open GUI), ``load_network`` on a COPY (never the active ``.inpx``),
``SimPeriod``/``RandSeed``/``NumRuns=1`` with a read-back, evaluation configured
AFTER load and BEFORE the run, one ``RunContinuous`` call, and a SIMULATIONRUN
row read back out of the result database before the run counts as finished.

It is deliberately NOT an import of that function. That runner takes a seed LIST
and owns the multi-seed job record, the trajectory arming and the per-seed
progress; a search candidate is exactly one seed, writes no trajectory (a
one-hour search would write gigabytes of per-vehicle rows nobody reads) and
carries a parameter application that has no equivalent there. Extracting a
shared helper would have to bend that function's contract around this one, and
its discipline is measured, load-bearing and covered by its own tests — so this
copies the SEQUENCE and cites it, rather than refactoring the thing that works.

**No test in this repository executes the COM half of this file**, and that is
stated rather than papered over: live Vissim is out of scope for offline tests,
and a "COM test" written against a mock proves only that the mock was called.

The COM half is exactly one method — :meth:`VissimSearchRunner._drive_vissim` —
and it is one method ON PURPOSE, so the boundary of that claim is a line you can
point at instead of a feeling. Everything else in this file is ordinary Python
over files, sqlite and a dict, and every bit of it IS covered offline by
``backend/tests/test_agent_search_job.py``: the candidate directory layout and
the clone (``_candidate_copy``), the stale-``.db`` sweep
(``_clear_stale_results``), the read-back verdict in both of its failure modes
(``_apply``), the NumRuns pin (``_pin_single_run``), the finished-run proof
including a CORRUPT database (``_run_completed``), the GEH extraction with
``None`` preserved (``geh_by_node``), the availability probe, and the
never-raises contract of :meth:`VissimSearchRunner.run` itself. That list was a
claim before 2026-08-21 and is a set of tests after it — the refuter found the
gap by reproducing a corrupt result database, which at the time raised straight
through the search.

The COM sequence itself is covered by inspection against the citation above and
by the live acceptance run that follows this unit.

WHY EVERY STEP RETURNS A REASON INSTEAD OF RAISING
--------------------------------------------------
``ParameterSearch._evaluate`` calls the runner with no handler of its own, so an
exception here does not lose one candidate — it unwinds the whole search into
``search_job``'s outer handler, which marks the job FAILED, and the FAILED path
writes no ``state.PARTIAL.NOT-MINTABLE.json``. One bad file therefore threw away
every measured ledger row the search had bought. So each step below answers with
a NAMED failure string, :meth:`VissimSearchRunner.run` is a guard around the
whole sequence, and the budget stays the only thing that can stop a search.
"""
from __future__ import annotations

import logging
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.agent.parameter_search import RunOutcome

logger = logging.getLogger(__name__)

#: The Protocol this implements and the dotted path of the implementation, both
#: reported by ``parameter_search.runner_requirement()`` so the trigger card can
#: NAME what would run rather than merely claiming something would.
RUNNER_INTERFACE = "services.agent.parameter_search.SearchRunner"
RUNNER_IMPLEMENTATION = "services.agent.search_runner.VissimSearchRunner"

# ---------------------------------------------------------------------------
# Named outcomes — a failed candidate is a REASON, never a silent zero
# ---------------------------------------------------------------------------

#: This machine cannot drive Vissim at all (not Windows, or pywin32 missing).
RUNNER_NOT_AVAILABLE = "RUNNER_NOT_AVAILABLE"

#: The dataset the candidates are cloned from is not on disk.
SOURCE_NETWORK_MISSING = "SOURCE_NETWORK_MISSING"

#: ``set_driving_behavior_parameter`` returned, but the read-back did not carry
#: the value that was asked for. Vissim fails silently on out-of-range writes,
#: so an unverified write is the exact failure this product exists to prevent.
PARAMETER_NOT_APPLIED = "PARAMETER_NOT_APPLIED"

#: RunContinuous returned but the result database does not prove a finished run
#: — absent, unreadable, CORRUPT, or carrying no SIMULATIONRUN row. All four are
#: the same answer to the same question ("did this run finish?") and all four are
#: answered with a reason rather than an exception: a half-flushed ``.db`` is a
#: thing Vissim really produces when a run is killed, and letting sqlite3 raise
#: out of :meth:`VissimSearchRunner.run` would abort a search that had already
#: spent an hour on the candidates before it (2026-08-21, refuter).
RUN_DID_NOT_COMPLETE = "RUN_DID_NOT_COMPLETE"

#: The candidate's own copy of the network could not be made — no disk, no
#: permission, a work dir that is not a directory. Distinct from
#: :data:`SOURCE_NETWORK_MISSING`, which is about the source; this is about the
#: destination, and it is the one failure that means no clone exists at all.
CANDIDATE_WORKSPACE_FAILED = "CANDIDATE_WORKSPACE_FAILED"

#: ``NumRuns`` could not be set to 1 or did not read back as 1. Named separately
#: from :data:`RUN_FAILED` because it has a specific, measured meaning: Vissim
#: would write N result databases for ONE requested run and the evidence readers
#: would SUM their volumes, so the GEH would come back N-fold wrong rather than
#: merely noisy.
NUM_RUNS_NOT_PINNED = "NUM_RUNS_NOT_PINNED"

#: The run finished and the reality check refused to grade it (its own status is
#: not CHECKED). Carried verbatim with the report's reason code appended.
SCORE_NOT_CHECKABLE = "SCORE_NOT_CHECKABLE"

#: Anything COM raised. The exception's text rides along.
RUN_FAILED = "RUN_FAILED"

RUNNER_REASON_CODES = (
    RUNNER_NOT_AVAILABLE,
    SOURCE_NETWORK_MISSING,
    CANDIDATE_WORKSPACE_FAILED,
    PARAMETER_NOT_APPLIED,
    NUM_RUNS_NOT_PINNED,
    RUN_DID_NOT_COMPLETE,
    SCORE_NOT_CHECKABLE,
    RUN_FAILED,
)

#: How close a read-back must be to the value that was written.
#:
#: Not exact equality: ``set_driving_behavior_parameter`` converts METRES to the
#: network's own display unit on the way in and back on the way out (see its
#: ``DRIVING_BEHAVIOR_PARAM_FIELDS`` unit_kind map), so a network displaying feet
#: round-trips 0.35 m through 1.148 ft. The tolerance is 1e-3, which is four
#: times finer than the narrowest published band's own resolution (0.1-0.4 m) and
#: still catches the failure this check exists for: a write Vissim SILENTLY
#: refused, which comes back as the old value, never as a 0.001 drift.
READBACK_TOLERANCE = 1e-3


# ---------------------------------------------------------------------------
# Availability — the same probe the simulation router uses, for the same reason
# ---------------------------------------------------------------------------


def runner_availability() -> tuple[bool, str | None]:
    """``(available, reason_when_not)`` — may THIS process drive Vissim?

    Mirrors ``routers/simulations._check_pywin32`` plus its ``os.name == "nt"``
    guard, which is the pair every COM entry point in this codebase already
    tests. Deliberately re-derived on every call rather than cached at import:
    the answer is a property of the machine, and a cached False taken during a
    test run would then be reported to a real user as the runner's real state.
    """
    import os

    if os.name != "nt":
        return False, (
            "VISSIM COM can run only on Windows with PTV Vissim installed; this backend "
            f"is running on {os.name!r}."
        )
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — an import failure IS the answer
        return False, f"pywin32/pythoncom is not available in this backend environment: {exc}"
    return True, None


def runner_available() -> bool:
    """Is a live runner wired on this machine? The ``wired`` flag's one source."""
    return runner_availability()[0]


# ---------------------------------------------------------------------------
# Scoring — the observation set is THREADED THROUGH, never re-resolved
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringContext:
    """Which observed data every candidate is graded against.

    Constructed ONCE, where the search's preconditions are evaluated
    (``services.agent.search_job.evaluate_start``), and carried from there into
    this runner unchanged. ``runner_requirement()`` is explicit about why: a
    candidate scored against a different observation set or window than the gate
    was opened on is not comparable to the baseline the gate measured, and the
    search would be optimising one number while reporting another.
    """

    period: str | None = None
    observation_set_id: str | None = None
    observation_period: str | None = None

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "observation_set_id": self.observation_set_id,
            "observation_period": self.observation_period,
        }


def _number(value: Any) -> float | None:
    """A finite float, or ``None``. ``parameter_search._number``'s rule, applied
    to the same field (``node_total_geh``) for the same reason."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def geh_by_node(
    report: Mapping[str, Any] | None, node_ids: Sequence[str]
) -> dict[str, float | None]:
    """``{node_id: GEH or None}`` for exactly the nodes asked about.

    ``None`` in three distinct situations, and it is the SAME ``None`` in all
    three because the ledger's question is "could this node be graded?":

    * the report has no entry for that node at all;
    * it has one whose ``node_total_geh`` is absent;
    * it has one whose ``node_total_geh`` is not a finite number.

    A zero is NEVER substituted. GEH 0 means a perfect match — the best score
    obtainable — so a missing statistic rendered as 0.0 would push an ungradable
    node to the front of ``ParameterSearch.best()`` and propose a write on
    evidence that does not exist. ``RunOutcome``'s own docstring pins this, and
    ``Score``/``score_holdout`` downstream count ``graded`` off exactly these
    Nones.
    """
    payload = report if isinstance(report, Mapping) else {}
    measured: dict[str, float | None] = {}
    for entry in payload.get("nodes") or ():
        if not isinstance(entry, Mapping):
            continue
        node_id = str(entry.get("node_id") or "")
        if node_id:
            measured[node_id] = _number(entry.get("node_total_geh"))
    return {str(node): measured.get(str(node)) for node in node_ids}


def report_refusal(report: Mapping[str, Any] | None) -> str | None:
    """The reason this report cannot be graded, or ``None`` when it can.

    The same reading ``parameter_search.evaluate_preconditions`` performs on the
    gate's own report: a status that is PRESENT and is not ``CHECKED`` is a
    refusal; an absent status is not read as one (a report built by a caller that
    does not stamp the field is graded on its nodes, which is where the numbers
    are). One rule, two call sites, no second opinion about what "checkable"
    means.
    """
    payload = report if isinstance(report, Mapping) else None
    if payload is None:
        return f"{SCORE_NOT_CHECKABLE}: no report"
    status = str(payload.get("status") or "")
    if status and status != "CHECKED":
        return f"{SCORE_NOT_CHECKABLE}: {payload.get('reason_code') or status}"
    return None


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


@dataclass
class VissimSearchRunner:
    """One candidate vector → one Vissim run → one GEH-per-node outcome.

    Constructed by ``services.agent.search_job`` on the job thread and used by
    ``parameter_search.ParameterSearch`` / ``accept_winner``. Never constructed
    in a request handler: every call inside :meth:`run` is COM, and CLAUDE.md's
    rule is that COM work goes through the job queue.

    Candidates run STRICTLY SEQUENTIALLY — one ``VissimController`` at a time,
    connected and disconnected inside :meth:`run` — because ``ParameterSearch``
    calls this synchronously, one vector per call. Two Vissim instances driven
    from one process is not a configuration this codebase has ever verified.
    """

    #: The dataset every candidate is CLONED from. The active dataset's ``.inpx``
    #: in production; never opened, never written, never loaded by this class.
    source_inpx: Path

    #: The job's own work dir. Every candidate gets a fresh subdirectory of it.
    work_dir: Path

    #: The driving behaviour the vector is written to. Chosen and validated
    #: before the search starts (``search_job.resolve_target_behaviour``), so the
    #: search and the proposal that follows it name the same object.
    behavior_no: int

    #: Simulation seconds per candidate run.
    duration_s: int

    #: The observed side. See :class:`ScoringContext`.
    scoring: ScoringContext = field(default_factory=ScoringContext)

    #: Called after every candidate with a small progress dict. The job's only
    #: window into a search that is mid-flight; it must never raise.
    on_candidate: Callable[[dict[str, Any]], None] | None = None

    #: Names the run in the scored report's provenance (``detail.scenario_id``).
    label: str = "parameter-search"

    #: How many candidates this runner has been asked for. Read by the job for
    #: its progress line; incremented before the run so a candidate that fails
    #: is still counted (it consumed a Vissim run and a slice of the budget).
    calls: int = 0

    def run(
        self, vector: Mapping[str, float], seed: int, node_ids: Sequence[str]
    ) -> RunOutcome:
        """``SearchRunner.run`` — never raises, always names its failure.

        The engine treats a non-ok outcome as an unusable point and carries the
        reason onto the ledger row. An exception escaping here does NOT merely
        lose one candidate: ``ParameterSearch._evaluate`` does not catch either,
        so it unwinds the whole search into ``search_job``'s outer handler, which
        marks the job FAILED — and the FAILED path writes no
        ``state.PARTIAL.NOT-MINTABLE.json``, so an hour of measured ledger rows
        is thrown away over one bad file. That is the failure the refuter
        reproduced on 2026-08-21 with a corrupt result database, and it is why
        this method is a GUARD around :meth:`_run_candidate` rather than the work
        itself.

        Two layers, deliberately:

        * every step below returns a NAMED failure string of its own, so the
          ledger says which step failed and why — that is the useful half;
        * this wrapper is the last-resort net, so the "never raises" contract
          holds even for a step that grows a new raise later. It is not an
          excuse for the first layer; a bare ``RUN_FAILED`` in a ledger is a
          diagnosis nobody can act on.
        """
        self.calls += 1
        index = self.calls
        started = time.monotonic()
        try:
            return self._run_candidate(index, vector, seed, node_ids, started)
        except Exception as exc:  # noqa: BLE001 — the contract is "never raises"
            logger.exception(
                "parameter search candidate %d raised out of its own error handling", index
            )
            return self._failed(
                index, vector, seed, started, f"{RUN_FAILED}: {type(exc).__name__}: {exc}"
            )

    def _run_candidate(
        self,
        index: int,
        vector: Mapping[str, float],
        seed: int,
        node_ids: Sequence[str],
        started: float,
    ) -> RunOutcome:
        """One candidate, step by step, each step naming its own failure."""
        available, reason = runner_availability()
        if not available:
            return self._failed(index, vector, seed, started, f"{RUNNER_NOT_AVAILABLE}: {reason}")
        source = Path(self.source_inpx)
        if not source.is_file():
            return self._failed(
                index, vector, seed, started, f"{SOURCE_NETWORK_MISSING}: {source}"
            )

        run_inpx, workspace_failure = self._candidate_copy(index, seed, source)
        if workspace_failure is not None:
            return self._failed(index, vector, seed, started, workspace_failure)

        drive_failure = self._drive_vissim(index, seed, run_inpx, vector)
        if drive_failure is not None:
            return self._failed(index, vector, seed, started, drive_failure)

        run_dir = self._candidate_dir(index, seed)
        completed = self._run_completed(run_dir)
        if completed is not None:
            return self._failed(index, vector, seed, started, completed)

        try:
            report = self._score(run_dir, index, seed)
        except Exception as exc:  # noqa: BLE001 — an unscorable run is a named failure
            logger.warning(
                "parameter search candidate %d ran but could not be scored (%s: %s)",
                index,
                type(exc).__name__,
                exc,
            )
            return self._failed(index, vector, seed, started, f"{SCORE_NOT_CHECKABLE}: {exc}")

        refusal = report_refusal(report)
        if refusal is not None:
            return self._failed(index, vector, seed, started, refusal)

        outcome = RunOutcome(
            ok=True,
            geh_by_node=geh_by_node(report, node_ids),
            elapsed_s=time.monotonic() - started,
        )
        self._progress(index, vector, seed, outcome)
        return outcome

    def _drive_vissim(
        self, index: int, seed: int, run_inpx: Path, vector: Mapping[str, float]
    ) -> str | None:
        """THE COM STAGE, and the only method in this file that touches Vissim.

        Isolated behind one seam for two reasons. It is the part no offline test
        can execute, so keeping it in one place is what lets every OTHER decision
        in this file be tested for real (a test stubs this method and drives the
        rest of :meth:`_run_candidate` end to end). And it is the part whose
        discipline is copied from ``routers/simulations._run_agent_simulation_job_locked``,
        so having it as one contiguous block is what makes that comparison
        readable.

        Returns a named failure, or ``None`` when the run really happened.
        """
        ctrl = None
        try:
            from services.vissim_com import VissimController

            ctrl = VissimController()
            # attach_to_running=False is a HARD safety rule for automation: the
            # default (GetActiveObject) attaches to a human's open Vissim GUI and
            # can destroy their unsaved work (docs/phase2/00-EXECUTION-GUIDE.md
            # §1 rule 3, and CLAUDE.md's COM safety block).
            ctrl.connect(visible=False, attach_to_running=False)
            ctrl.load_network(str(run_inpx))
            ctrl.set_sim_duration(int(self.duration_s))
            ctrl.set_random_seed(int(seed))

            mismatch = self._apply(ctrl, vector)
            if mismatch is not None:
                return mismatch

            pinned = self._pin_single_run(ctrl, index, seed)
            if pinned is not None:
                return pinned
            # Evaluations are not retroactive (00-EXECUTION-GUIDE.md §3.1): after
            # load_network, before RunContinuous, once per run. Without it the
            # result database carries only the SIMULATIONRUN table and the reality
            # check has no movement volumes to grade — a silent KPI-less
            # "success", which is the failure mode this whole product refuses.
            ctrl.configure_network_performance_evaluation(duration_s=int(self.duration_s))
            # ONE COM call that runs to SimPeriod internally. Never a
            # RunSingleStep loop (minutes for 60 sim-seconds) and never
            # SimBreakAt-chunked for a progress bar: perturbing a run's behaviour
            # to sample it is forbidden by the same section.
            ctrl.vissim.Simulation.RunContinuous()
        except Exception as exc:  # noqa: BLE001 — a COM failure is a named candidate failure
            logger.warning(
                "parameter search candidate %d failed before scoring (%s: %s)",
                index,
                type(exc).__name__,
                exc,
            )
            return f"{RUN_FAILED}: {exc}"
        finally:
            if ctrl is not None:
                try:
                    ctrl.disconnect()
                except Exception:  # noqa: BLE001 — a failed disconnect must not mask the run
                    logger.exception("Error disconnecting the parameter-search Vissim instance")
        return None

    # -- the steps, each one citing what it mirrors ------------------------

    def _candidate_dir(self, index: int, seed: int) -> Path:
        """One directory per (candidate, seed). The seed is in the NAME because
        ``accept_winner`` re-runs the winner and the baseline at an unseen seed:
        without it the holdout run would land in the calibration run's directory
        and its result database would be read as that run's."""
        return Path(self.work_dir) / f"cand_{index:04d}_seed_{int(seed)}"

    def _candidate_copy(
        self, index: int, seed: int, source: Path
    ) -> tuple[Path, str | None]:
        """The candidate's own copy of the network, or a named failure.

        ``shutil.copyfile`` into a fresh per-candidate directory — the same clone
        discipline ``_run_agent_simulation_job_locked`` uses per seed, and the
        reason CLAUDE.md gives for it: the ACTIVE dataset's ``.inpx`` is never
        edited in place, because a human may have it open and because a search
        that crashed halfway would otherwise leave the project's own network
        carrying a rejected candidate's numbers.

        Returns ``(path, None)`` or ``(path, reason)``. A full disk, a read-only
        work dir and a work dir that is really a file are all ordinary operating
        conditions on a consultant's machine, and none of them is a reason to
        throw away the candidates a search has already measured.
        """
        run_dir = self._candidate_dir(index, seed)
        run_inpx = run_dir / source.name
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._clear_stale_results(run_dir)
            shutil.copyfile(source, run_inpx)
        except OSError as exc:
            logger.warning(
                "parameter search candidate %d could not be cloned into %s (%s: %s)",
                index,
                run_dir,
                type(exc).__name__,
                exc,
            )
            return run_inpx, (
                f"{CANDIDATE_WORKSPACE_FAILED}: could not clone the network into {run_dir} "
                f"({type(exc).__name__}: {exc})"
            )
        return run_inpx, None

    @staticmethod
    def _clear_stale_results(directory: Path) -> list[Path]:
        """Enforce, rather than assume, that nothing pre-existing can be read as
        THIS candidate's result (``routers/simulations._clear_stale_result_dbs``
        states the rule; this is its equivalent, kept local because a service
        importing a router would invert this codebase's layering).

        Recursive, because ``_run_completed`` reads recursively: a stale ``.db``
        one directory down would be found by the check and missed by the sweep.
        Returns what it removed so a test can prove the sweep really swept.
        """
        removed: list[Path] = []
        for stale in list(directory.rglob("*.db")):
            try:
                stale.unlink()
                removed.append(stale)
            except OSError:
                logger.warning("Could not remove a stale result file at %s", stale)
        return removed

    def _apply(self, ctrl: Any, vector: Mapping[str, float]) -> str | None:
        """Write the vector ONE FIELD AT A TIME, read every one back.

        ``set_driving_behavior_parameter`` is the whitelisted, unit-converting
        setter and it returns ``get_driving_behavior``'s full post-write dict, so
        the read-back costs nothing extra and is not optional here: Vissim
        accepts an out-of-range ``SetAttValue`` silently and leaves the old value
        in place. A candidate scored on the values it MEANT to set rather than
        the ones the model holds is a whole search built on a lie.

        Returns a named refusal string, or ``None`` when every field landed.

        NOT routed through ``tools_edit``/``GuardrailEngine`` on purpose. The
        guardrail governs the APPROVED write to the live model — the one a human
        clicks, on a shared behaviour, with a re-minted grant. These are
        throwaway writes on a throwaway clone that no one will ever open, and
        constructing a write gate for each of them would put the shared-object
        refusal (which this candidate would legitimately trip) between the search
        and every measurement it exists to take.
        """
        for parameter, value in sorted(dict(vector).items()):
            target = float(value)
            try:
                readback = ctrl.set_driving_behavior_parameter(
                    int(self.behavior_no), parameter, target
                )
            except Exception as exc:  # noqa: BLE001 — a refused write is a NAMED failure
                # ``set_driving_behavior_parameter`` raises whatever Vissim's own
                # SetAttValue raises for a value past a floor/ceiling. That is the
                # SAME event as a silent non-write from this runner's point of
                # view — the model does not hold the number this candidate would
                # be scored on — so it gets the same code rather than the generic
                # one, and the candidate is skipped instead of the search dying.
                return (
                    f"{PARAMETER_NOT_APPLIED}: behaviour {self.behavior_no} {parameter} = {target} "
                    f"was refused by Vissim ({type(exc).__name__}: {exc})"
                )
            landed = _number((readback or {}).get(parameter))
            if landed is None or not math.isclose(
                landed, target, rel_tol=READBACK_TOLERANCE, abs_tol=READBACK_TOLERANCE
            ):
                return (
                    f"{PARAMETER_NOT_APPLIED}: behaviour {self.behavior_no} {parameter} was set to "
                    f"{target} but read back as {landed!r}"
                )
        return None

    @staticmethod
    def _pin_single_run(ctrl: Any, index: int, seed: int) -> str | None:
        """NumRuns=1, set and READ BACK, exactly as the agent run does.

        MEASURED 2026-08-07 by that runner (its own call site carries the
        finding): a real consultant export carries ``numRuns="5"``,
        ``RunContinuous`` honours it, and one requested run came back as five
        result databases whose volumes the evidence readers then SUMMED. For a
        search that would be five-fold wrong GEH on every candidate. Fail closed:
        a bad set or read-back refuses the candidate.

        Refuses by RETURNING a named reason rather than raising. The agent run
        raises here because a simulation job has one run and nothing to salvage;
        a search has a ledger of measured candidates behind it, and one unpinned
        network must cost that search one candidate, not all of them.
        """
        try:
            ctrl.vissim.Simulation.SetAttValue("NumRuns", 1)
            num_runs = int(ctrl.vissim.Simulation.AttValue("NumRuns"))
        except Exception as exc:  # noqa: BLE001 — an unpinnable network is a named failure
            return (
                f"{NUM_RUNS_NOT_PINNED}: could not force NumRuns=1 for candidate {index} "
                f"(seed {seed}) ({type(exc).__name__}: {exc})"
            )
        if num_runs != 1:
            return (
                f"{NUM_RUNS_NOT_PINNED}: NumRuns read back as {num_runs} for candidate {index} "
                f"(seed {seed}) -- refusing to score: Vissim would write that many result "
                "databases for ONE requested run and their volumes would be summed into wrong GEH."
            )
        return None

    @staticmethod
    def _run_completed(run_dir: Path) -> str | None:
        """``None`` when the run really finished, a named refusal otherwise.

        The artifact is a SIMULATIONRUN row in the result database — never
        ``SimSec``, which resets to 0 the moment a run ends
        (00-EXECUTION-GUIDE.md §3.1). Same check, same reason, as the agent run.

        NEVER RAISES, and that is the point (2026-08-21, refuter). A result
        database that Vissim half-flushed — a killed run, a full disk, a file the
        antivirus is still holding — makes ``sqlite3`` raise, and this method used
        to let it: straight out through ``ParameterSearch._evaluate`` (which does
        not catch either) and into the job's outer handler, which marks the job
        FAILED and, on that path, writes no partial-state artifact at all. One
        corrupt file cost an hour of measured ledger rows. It is now the same
        answer as an absent database, because it is the same question: this run
        did not demonstrably finish, so this candidate is not scorable.
        """
        import sqlite3

        try:
            db_paths = sorted(run_dir.rglob("*.db"), key=lambda path: path.stat().st_mtime)
        except OSError as exc:
            return (
                f"{RUN_DID_NOT_COMPLETE}: the result directory could not be read "
                f"({type(exc).__name__}: {exc})"
            )
        if not db_paths:
            return f"{RUN_DID_NOT_COMPLETE}: RunContinuous wrote no result database"
        try:
            conn = sqlite3.connect(str(db_paths[-1]))
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — a corrupt db is an ANSWER, not a crash
            return (
                f"{RUN_DID_NOT_COMPLETE}: the result database at {db_paths[-1].name} could not be "
                f"read ({type(exc).__name__}: {exc})"
            )
        if "SIMULATIONRUN" not in {str(row[0]).upper() for row in rows}:
            return f"{RUN_DID_NOT_COMPLETE}: the result database carries no SIMULATIONRUN row"
        return None

    def _score(self, run_dir: Path, index: int, seed: int) -> dict[str, Any]:
        """``build_reality_check_report`` over THIS candidate's result directory.

        Imported here rather than at module scope for the reason every router and
        service in this package states at its own deferred import of it:
        ``tools_metrics`` pulls in the whole tool registry, and importing it
        early reorders tool registration.

        ``result_dir`` is passed instead of letting the report resolve a scenario
        id. A candidate is not a scenario: it has no job record, it must never
        appear in the run picker, and the resolver's workspace/revision checks
        exist to answer "may this caller read someone else's run" — a question
        with no meaning here, where the caller produced the directory itself,
        moments ago, on a thread bound to the workspace that asked for it.
        """
        from services.agent.tools_metrics import build_reality_check_report

        return build_reality_check_report(
            f"{self.label}:candidate-{index}:seed-{seed}",
            result_dir=run_dir,
            **self.scoring.as_kwargs(),
        )

    # -- bookkeeping -------------------------------------------------------

    def _failed(
        self,
        index: int,
        vector: Mapping[str, float],
        seed: int,
        started: float,
        reason: str,
    ) -> RunOutcome:
        outcome = RunOutcome(
            ok=False,
            geh_by_node={},
            reason=reason,
            elapsed_s=time.monotonic() - started,
        )
        self._progress(index, vector, seed, outcome)
        return outcome

    def _progress(
        self,
        index: int,
        vector: Mapping[str, float],
        seed: int,
        outcome: RunOutcome,
    ) -> None:
        """Tell the job what just happened. Never raises: a progress line that
        killed a running search would be the tail wagging the dog."""
        if self.on_candidate is None:
            return
        try:
            self.on_candidate(
                {
                    "index": index,
                    "seed": int(seed),
                    "params": {str(k): float(v) for k, v in dict(vector).items()},
                    "ok": bool(outcome.ok),
                    "reason": outcome.reason,
                    "elapsed_s": float(outcome.elapsed_s or 0.0),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("A parameter-search progress callback raised; the search continues")


__all__ = [
    "CANDIDATE_WORKSPACE_FAILED",
    "NUM_RUNS_NOT_PINNED",
    "PARAMETER_NOT_APPLIED",
    "READBACK_TOLERANCE",
    "RUNNER_IMPLEMENTATION",
    "RUNNER_INTERFACE",
    "RUNNER_NOT_AVAILABLE",
    "RUNNER_REASON_CODES",
    "RUN_DID_NOT_COMPLETE",
    "RUN_FAILED",
    "SCORE_NOT_CHECKABLE",
    "SOURCE_NETWORK_MISSING",
    "ScoringContext",
    "VissimSearchRunner",
    "geh_by_node",
    "report_refusal",
    "runner_availability",
    "runner_available",
]

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
over files, sqlite and a dict — INCLUDING the candidate materialization, which
is a pure XML transform — and every bit of it IS covered offline by
``backend/tests/test_agent_search_job.py``: the candidate directory layout, the
materialization and its equivalence to ``ArtifactManager.clone()``
(``_candidate_copy``), the sibling assets that travel with it, the standalone
read-back and the proof that no unloadable file reaches the COM stage
(``_standalone_blocker``), the stale-``.db`` sweep (``_clear_stale_results``),
the read-back verdict in both of its failure modes and the constraint-aware write
ORDER (``_apply``, ``apply_order``), the vector feasibility pre-check
(``infeasible_coupling``), the instance-reap discipline in all four of its
branches (``_reap_vissim``, ``_shutdown``), the NumRuns pin (``_pin_single_run``),
the finished-run proof including a CORRUPT database and the location Vissim
actually writes to (``_run_completed``), the GEH extraction with ``None``
preserved (``geh_by_node``), the availability probe, and the never-raises
contract of :meth:`VissimSearchRunner.run` itself. That list was a claim before
2026-08-21 and is a set of tests after it — four times over. The refuter closed
the first gap by reproducing a corrupt result database, which at the time raised
straight through the search; two live acceptance runs closed three more (below).

The COM sequence itself is covered by inspection against the citation above and
by the live acceptance run that follows this unit.

WHAT THAT INSPECTION COULD NOT SEE — MEASURED 2026-08-21, LIVE
---------------------------------------------------------------
The first live acceptance run failed on EVERY candidate with
``RUN_FAILED: ... 'The network file could not be loaded.'`` (Vissim 2026 COM,
``-2147352567``), and an isolation probe reproduced it without this runner at
all: a bare ``shutil.copyfile`` of the workspace's ``current.inpx`` into a temp
directory cannot be ``LoadNet``-ed. **The raw upload is not standalone-loadable.**
The client export is a SCENARIO-MANAGED project — it ships beside
``project.vissimpdb`` and ``Scenarios/`` and self-declares
``<netPara underScenMngm="true">`` — and Vissim refuses ``LoadNet`` on such a
file, with a generic error that names nothing.

This runner's COM SEQUENCE was right; its INPUT was not. Every working run path
in this product materializes a standalone copy first, and the comparison against
``routers/simulations._run_agent_simulation_job_locked`` missed it because that
function's ``source_inpx`` is ALREADY materialized before it is called — it is
the session's ``ArtifactManager`` working copy, never the raw dataset. Reading
the two functions side by side showed identical COM discipline and could not show
that one of them is handed a different KIND of file. The live evidence is one
byte: ``working.inpx`` is 1,391,687 B against the raw upload's 1,391,686 —
exactly the ``"true"`` → ``"false"`` flip.

So :meth:`VissimSearchRunner._candidate_copy` no longer copies; it MATERIALIZES,
through the same functions ``ArtifactManager.clone()`` uses (see that method for
which seam and why), and reads the flag back before any COM runs. The general
lesson, recorded because it will recur: **a seam comparison cannot see a
precondition on the seam's INPUT.** Two call sites can run identical code on
inputs only one of them has prepared.

LIVE FINDINGS #2 AND #3 — MEASURED 2026-08-21, RUN 2
-----------------------------------------------------
Run 2 got past ``LoadNet`` (candidate 1 ok, 63 s) and then lost 12 of the next
13 candidates to ONE Vissim message, quoted here verbatim because it is the
evidence that the constraint exists:

    "AttValue failed: The default min. lateral distance while standing for
     driving behavior parameter set 1 ... must be less than or equal to the
     default min. lateral distance while driving."

**#2, the coupling.** ``lat_dist_stand_def <= lat_dist_driv_def``, enforced at
SET time on the model as it stands mid-write. That single message hid two
different defects:

* a FEASIBLE target dying of write ORDER — behaviour 1 sat at
  ``(stand=1.0, driv=1.0)`` and a candidate asked for ``(0.25, 0.45)``; writing
  ``driv`` first (the alphabetical order this file used) made the model
  momentarily ``(1.0, 0.45)`` and Vissim refused a vector it would have
  accepted. Fixed by :func:`apply_order`, whose algebra proves at least one of
  the two orders always keeps every intermediate state legal;
* an INFEASIBLE target costing a full Vissim start-up (~17 s) to be refused —
  the LHS screen samples each dimension independently inside its published band,
  so about half its samples ask for ``stand > driv``. Fixed by
  :func:`infeasible_coupling`, which answers in microseconds and by name.
  **#51's search space is unchanged**: the box is the guideline's own bands and
  that design is immutable. The runner refusing the impossible corner cheaply is
  the minimal honest fix; the compass treats those rows as failed candidates and
  navigates on, which is what a failed candidate is for.

Both read ONE declared table, :data:`PARAMETER_COUPLINGS`, so the next measured
coupling is a row and not an edit. WHY THE CONSTRAINT ENGINE DID NOT KNOW THIS
(checked 2026-08-21): ``constraints.py``'s #85 catalogue is per-field floors and
ceilings, live-verified by ``tools/probe_driving_behaviors.py``, which set each
scalar ALONE against a baseline where this coupling happened to hold. **A
per-field probe cannot see a cross-field constraint** — the same shape of blind
spot as finding #1, one level up. Teaching the preflight engine the pairwise rule
is a follow-up unit; what the runner owes is to stop paying COM to rediscover it.

**#3, the instance leak.** After ~13 candidates, THREE ``Vissim.exe`` processes
were still alive, started at candidate times. Measured cause:
``VissimController.disconnect()`` DOES call ``Exit()`` (guarded on
``_owns_instance``, which ``connect(attach_to_running=False)`` sets True via
``Dispatch``) — but ``Exit()`` is a REQUEST, not a join, and nothing anywhere in
this product waits for the process to be gone. ``routers/simulations.py``'s
per-seed lifecycle has the identical pattern and therefore the identical gap; it
never accumulates only because a job runs one to five seeds and then ends. A
search runs hundreds in one thread, which is why it is the first caller that
could not get away with it. Fixed by :func:`_reap_vissim` — a bounded wait, then
termination of ONLY the pids that appeared after this candidate connected, so a
modeller's own open Vissim is untouchable by construction.

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
import os
import shutil
import subprocess
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

#: The candidate was copied but is still scenario-managed, so ``LoadNet`` would
#: refuse it. MEASURED 2026-08-21 (see the module docstring): this is the state
#: the first live acceptance ran fifty candidates in, each one failing at COM
#: with an error that named nothing. Caught BEFORE the COM stage now, by reading
#: the flag back off the materialized file — a candidate that cannot be loaded is
#: knowable from its own bytes, and finding that out over COM costs a Vissim
#: start-up per candidate to learn nothing.
CANDIDATE_NOT_STANDALONE = "CANDIDATE_NOT_STANDALONE"

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

#: A candidate vector Vissim could not hold at all, because two of its fields
#: are coupled and this vector violates the coupling. MEASURED 2026-08-21 (live
#: acceptance run 2): the LHS screen samples each dimension independently inside
#: its published band, so roughly half of its samples ask for
#: ``lat_dist_stand_def > lat_dist_driv_def`` — which Vissim rejects at
#: ``SetAttValue`` time. Before this code those cost a full Vissim start-up each
#: (~17 s) to learn something arithmetic could have said in microseconds.
VECTOR_INFEASIBLE_COUPLING = "VECTOR_INFEASIBLE_COUPLING"

RUNNER_REASON_CODES = (
    RUNNER_NOT_AVAILABLE,
    SOURCE_NETWORK_MISSING,
    VECTOR_INFEASIBLE_COUPLING,
    CANDIDATE_WORKSPACE_FAILED,
    CANDIDATE_NOT_STANDALONE,
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
# Instance lifecycle — a candidate's Vissim must be GONE before the next starts
# ---------------------------------------------------------------------------

#: How long to wait for a candidate's own Vissim process to disappear after
#: ``disconnect()`` before terminating it. MEASURED 2026-08-21 (live acceptance
#: run 2): after ~13 candidates THREE Vissim.exe processes were still alive,
#: started at candidate times. Generous, because a normal teardown is seconds
#: and this only ever costs time on an instance that is already misbehaving.
VISSIM_EXIT_GRACE_S = 30.0

#: How often the wait re-checks.
VISSIM_EXIT_POLL_S = 0.5


def vissim_pids() -> set[int]:
    """Every running ``Vissim*`` process id, or an empty set if it cannot be asked.

    Windows ``tasklist``, because ``psutil`` IS NOT INSTALLED in this backend
    (measured 2026-08-21 — ``routers/assistant.py`` imports it inside a
    ``try/except ImportError`` for exactly that reason). An empty set is the safe
    answer: :func:`_reap_vissim` treats "cannot enumerate" as "terminate nothing",
    so a box where this fails degrades to today's behaviour rather than to
    killing something it cannot see properly.

    ``tools/calibration_loop_live_check.py`` carries the same fifteen lines; a
    service cannot import from ``tools/``. The natural home for both is
    ``services/vissim_com.py``, next to ``connect``/``disconnect`` — a
    consolidation this unit deliberately does not make, because changing
    ``disconnect()`` changes every COM caller in the product and this round
    cannot re-verify them live.
    """
    if os.name != "nt":
        return set()
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) < 2:
            continue
        if parts[0].strip('"').lower().startswith("vissim"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def terminate_pids(pids: Sequence[int]) -> list[int]:
    """Force-terminate the given process ids. Returns the ones the call accepted."""
    killed: list[int] = []
    for pid in sorted(set(int(pid) for pid in pids)):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, timeout=60
            )
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


def _reap_vissim(
    owned_before: set[int],
    *,
    grace_s: float = VISSIM_EXIT_GRACE_S,
    poll_s: float = VISSIM_EXIT_POLL_S,
    list_pids: Callable[[], set[int]] | None = None,
    terminate: Callable[[Sequence[int]], list[int]] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Wait for THIS candidate's Vissim to be gone; terminate it if it will not.

    WHY THIS EXISTS, measured 2026-08-21. ``VissimController.disconnect()`` does
    call ``Exit()`` (it is guarded on ``_owns_instance``, and
    ``connect(attach_to_running=False)`` sets that True via ``Dispatch``) — but
    ``Exit()`` is a REQUEST, not a join. Nothing in this product waits for the
    process to actually be gone, so "gone before the next candidate starts" was
    never established anywhere. Live run 2 found three survivors after thirteen
    candidates.

    ``routers/simulations.py``'s per-seed lifecycle has the IDENTICAL pattern and
    therefore the identical gap; it simply never accumulates, because a job runs
    one to five seeds and then the request is over. A search runs hundreds in one
    thread, which turns a tolerable non-determinism into memory and licence
    death. So this is not "the search doing it differently" — it is the search
    being the first caller that could not get away with it.

    SAFETY, and it is the whole design: ``owned_before`` is the set of Vissim
    pids that existed BEFORE this candidate connected. Only pids that appeared
    after it are ever waited on or terminated. A modeller's own open Vissim was
    in ``owned_before`` and is untouchable by construction — the same rule
    ``tools/calibration_loop_live_check.py`` follows, and the same one
    CLAUDE.md's COM safety block is about.

    Every collaborator is injectable so the discipline is testable with no COM
    and no processes.
    """
    list_pids = list_pids or vissim_pids
    terminate = terminate or terminate_pids
    sleep = sleep or time.sleep

    deadline = time.monotonic() + max(float(grace_s), 0.0)
    survivors: set[int] = set()
    while True:
        survivors = set(list_pids()) - set(owned_before)
        if not survivors:
            return {"survivors": [], "terminated": [], "waited": True}
        if time.monotonic() >= deadline:
            break
        sleep(max(float(poll_s), 0.0))

    logger.warning(
        "parameter search: %d Vissim process(es) survived Exit() by %.0fs — terminating %s "
        "(pids present before this candidate are never touched: %s)",
        len(survivors),
        grace_s,
        sorted(survivors),
        sorted(owned_before) or "none",
    )
    terminated = terminate(sorted(survivors))
    return {"survivors": sorted(survivors), "terminated": terminated, "waited": True}


# ---------------------------------------------------------------------------
# Scoring — the observation set is THREADED THROUGH, never re-resolved
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringContext:
    """Which observed data every candidate is graded against, and against WHICH
    objects its misses are attributed.

    Constructed ONCE, where the search's preconditions are evaluated
    (``services.agent.search_job.evaluate_start``), and carried from there into
    this runner unchanged. ``runner_requirement()`` is explicit about why: a
    candidate scored against a different observation set or window than the gate
    was opened on is not comparable to the baseline the gate measured, and the
    search would be optimising one number while reporting another.

    ``targets`` (2026-08-23) is frozen here for the same reason and not a
    weaker one. The gate is evaluated on a report built WITH the server-derived
    edit targets; a candidate graded without them would produce a report whose
    every miss reads ``NO_TARGET_OBJECT`` — same GEH numbers, different account
    of what they mean — and the acceptance story would be told from one report
    while the gate was opened on another.
    """

    period: str | None = None
    observation_set_id: str | None = None
    observation_period: str | None = None
    #: ``tools_metrics.RealityCheckNodeTarget`` instances. Typed ``Any`` on
    #: purpose: naming the class here would mean importing ``tools_metrics`` at
    #: module scope, which pulls in the whole tool registry and reorders tool
    #: registration — the trap :meth:`VissimSearchRunner._score` defers its own
    #: import to avoid. A TUPLE, because this dataclass is frozen and a frozen
    #: value that holds a mutable list is only half frozen.
    targets: tuple[Any, ...] = ()

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "observation_set_id": self.observation_set_id,
            "observation_period": self.observation_period,
            # ``None`` rather than ``[]``: ``build_reality_check_report`` treats
            # an empty list and no list identically, and the absence is the
            # honest word for "nothing could be derived".
            "targets": list(self.targets) or None,
        }

    def as_record(self) -> dict[str, Any]:
        """The JSON-safe half, for the job record.

        :meth:`as_kwargs` carries target OBJECTS bound for
        ``build_reality_check_report``; a job record is a payload other code may
        one day serialise, so it gets the node ids and the count instead of
        models ``json.dumps`` would refuse.
        """
        payload = {key: value for key, value in self.as_kwargs().items() if key != "targets"}
        payload["target_nodes"] = sorted(
            {str(getattr(target, "node_id", "")) for target in self.targets}
        )
        payload["target_count"] = len(self.targets)
        return payload


# ---------------------------------------------------------------------------
# Field couplings — constraints BETWEEN two parameters, as DATA
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldCoupling:
    """``lower <= upper``, enforced by Vissim at ``SetAttValue`` time.

    A TABLE, not an ``if``, because the next measured coupling should land here
    as a row and change nothing else. Both the feasibility pre-check
    (:func:`infeasible_coupling`) and the write ordering (:func:`apply_order`)
    read this same table, so a new row is honoured by both at once.
    """

    #: The parameter that must be less than or equal to the other.
    lower: str
    upper: str
    #: Vissim's OWN sentence, verbatim from the live COM error. Quoted rather
    #: than paraphrased: it is the evidence that this coupling exists, and a
    #: paraphrase is this product asserting a constraint on its own authority.
    vissim_message: str
    #: When, and how, this was measured.
    measured: str


#: Every coupling this product has MEASURED. One row today.
#:
#: WHY IT IS NOT IN ``constraints.py`` #85 (checked 2026-08-21): that catalogue
#: is per-field floors and ceilings, live-verified by
#: ``tools/probe_driving_behaviors.py``, which set each whitelisted scalar ALONE
#: against a baseline behaviour where this coupling happened to hold. A
#: per-field probe cannot see a cross-field constraint — the same shape of blind
#: spot as this unit's other two live findings. Adding a pairwise rule to the
#: preflight constraint engine is a follow-up unit, not this runner's call; what
#: the runner owes is to stop paying a Vissim start-up to rediscover it.
PARAMETER_COUPLINGS: tuple[FieldCoupling, ...] = (
    FieldCoupling(
        lower="lat_dist_stand_def",
        upper="lat_dist_driv_def",
        vissim_message=(
            "The default min. lateral distance while standing for driving behavior parameter "
            "set N ... must be less than or equal to the default min. lateral distance while "
            "driving."
        ),
        measured=(
            "2026-08-21, live acceptance run 2: 12 of 13 candidates died on this at "
            "SetAttValue time, behaviour 1 at baseline (driv=1.0, stand=1.0)"
        ),
    ),
)


def infeasible_coupling(vector: Mapping[str, float]) -> str | None:
    """``None`` when this vector can exist in Vissim at all, a named reason otherwise.

    Checked on the VECTOR alone, before anything is copied or connected, because
    a vector whose own two fields contradict each other cannot be applied in ANY
    order — no amount of care at write time helps, and a Vissim start-up to be
    told so costs ~17 s of a budget that IS the candidate count.

    Only fires when BOTH fields of a coupling are present. A vector that moves
    just one of them is a question about the MODEL's current other value, not
    about the vector, and it is answered where that value is known — by
    :meth:`VissimSearchRunner._apply`'s read-back, as ``PARAMETER_NOT_APPLIED``.

    NOTE what this deliberately does NOT do: it does not narrow #51's search
    space. That design is immutable, the box is the guideline's own published
    bands, and the LHS screen samples the dimensions independently by
    construction. The honest fix is for the RUNNER to refuse the impossible
    corner by name and cheaply; the compass then treats those rows as failed
    candidates and navigates on, which is exactly what a failed candidate is for.
    """
    values = dict(vector)
    for coupling in PARAMETER_COUPLINGS:
        low = _number(values.get(coupling.lower))
        high = _number(values.get(coupling.upper))
        if low is None or high is None:
            continue
        if low > high:
            return (
                f"{VECTOR_INFEASIBLE_COUPLING}: {coupling.lower}={low} > "
                f"{coupling.upper}={high}, which Vissim refuses at set time — "
                f'"{coupling.vissim_message}" (measured {coupling.measured})'
            )
    return None


def apply_order(
    vector: Mapping[str, float], current: Mapping[str, Any] | None
) -> list[str]:
    """The order to write these fields in so EVERY intermediate state is legal.

    THE PROBLEM, measured 2026-08-21: Vissim enforces a coupling at set time, on
    the model as it stands mid-write. Writing one field at a time therefore has
    an order, and the wrong one refuses a target that is perfectly feasible. The
    live case: behaviour 1 sat at (stand=1.0, driv=1.0) and a candidate asked for
    (stand=0.25, driv=0.45); writing ``driv`` first makes the model momentarily
    (stand=1.0, driv=0.45) — stand > driv — and Vissim refuses.

    THE RULE, with its algebra. Write ``S`` for the lower field and ``D`` for the
    upper; ``S0, D0`` is the model now and ``S1, D1`` the target. The model is
    legal now, so ``S0 <= D0``; the target is feasible (checked by
    :func:`infeasible_coupling`), so ``S1 <= D1``. There are two orders and each
    has exactly one intermediate state:

        lower first:  (S1, D0)   legal iff  S1 <= D0
        upper first:  (S0, D1)   legal iff  S0 <= D1

    **At least one is always legal.** Suppose neither were: ``S1 > D0`` and
    ``S0 > D1``. From ``S1 > D0 >= S0`` we get ``S1 > S0``; from
    ``S0 > D1 >= S1`` we get ``S0 > S1``. Both cannot hold, so the assumption is
    false. ∎

    So the rule is a single test — **lower first iff ``S1 <= D0``** — and its
    else-branch is provably safe rather than merely hopeful: ``not (S1 <= D0)``
    gives ``S1 > D0 >= S0``, hence ``S0 < S1 <= D1``, so ``S0 <= D1`` and the
    upper-first intermediate is legal. Equality is fine on both sides because the
    constraint is ``<=``: a target of ``S1 == D0`` takes the lower-first branch
    and lands on a legal ``(S1, D0)``.

    ``current`` is the behaviour as Vissim holds it right now (``D0``). Without
    it the order falls back to the deterministic sorted one — a write that then
    fails is caught by the read-back and named, never assumed to have landed.
    """
    order = sorted(str(name) for name in vector)
    state = dict(current or {})
    for coupling in PARAMETER_COUPLINGS:
        if coupling.lower not in order or coupling.upper not in order:
            continue
        target_lower = _number(dict(vector).get(coupling.lower))
        current_upper = _number(state.get(coupling.upper))
        if target_lower is None or current_upper is None:
            continue
        first, second = (
            (coupling.lower, coupling.upper)
            if target_lower <= current_upper
            else (coupling.upper, coupling.lower)
        )
        # A SWAP, not a rebuild: everything not in this pair keeps its place, so
        # the order stays deterministic and a second coupling row cannot silently
        # undo the first one's decision about an unrelated field.
        i, j = order.index(first), order.index(second)
        if i > j:
            order[i], order[j] = order[j], order[i]
    return order


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

    #: Every Vissim process this runner had to terminate because it survived
    #: ``Exit()`` (see :func:`_reap_vissim`). Non-empty means the box is leaking
    #: instances and the search is the only thing stopping it; carried so a long
    #: run can be diagnosed after the fact instead of by watching Task Manager.
    reaped_pids: list[int] = field(default_factory=list)

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

        # BEFORE the copy, let alone the connect. A vector whose own fields
        # contradict each other cannot be applied in any order, so every byte
        # and every second spent on it is spent to learn what arithmetic already
        # knows (see ``infeasible_coupling``).

        infeasible = infeasible_coupling(vector)
        if infeasible is not None:
            return self._failed(index, vector, seed, started, infeasible)

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

        THE INSTANCE MUST BE GONE WHEN THIS RETURNS, and that is now enforced
        rather than assumed — see :func:`_reap_vissim` for the measurement that
        made it necessary. ``owned_before`` is captured BEFORE the connect so the
        reap can only ever touch a process this candidate started.
        """
        ctrl = None
        owned_before = vissim_pids()
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
            self._shutdown(ctrl, owned_before)
        return None

    def _shutdown(self, ctrl: Any, owned_before: set[int]) -> None:
        """Ask the instance to exit, then MAKE SURE it did.

        Two steps, and the second is the one live run 2 showed was missing:

        1. ``disconnect()`` — the house call. Measured: it does call ``Exit()``
           (guarded on ``_owns_instance``, which ``connect(attach_to_running=False)``
           sets True via ``Dispatch``), and it drops this process's reference.
        2. :func:`_reap_vissim` — because ``Exit()`` is a request, not a join.
           Nothing anywhere in this product waited for the process to be gone,
           so it was gone only by luck; thirteen candidates left three alive.

        Runs in ``finally``, so it happens on the failure path too — which is the
        path that matters, since a candidate that failed at ``LoadNet`` or at a
        refused ``SetAttValue`` still started a Vissim.
        """
        if ctrl is not None:
            try:
                ctrl.disconnect()
            except Exception:  # noqa: BLE001 — a failed disconnect must not mask the run
                logger.exception("Error disconnecting the parameter-search Vissim instance")
        try:
            reaped = _reap_vissim(owned_before)
        except Exception:  # noqa: BLE001 — housekeeping must never fail a candidate
            logger.exception("parameter search: the Vissim reap failed")
            return
        if reaped.get("terminated"):
            self.reaped_pids.extend(int(pid) for pid in reaped["terminated"])

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
        """MATERIALIZE the candidate's own standalone network, or name the failure.

        Not a copy. MEASURED 2026-08-21 (see the module docstring): a bare
        ``shutil.copyfile`` of a scenario-managed export produces a file Vissim
        will not ``LoadNet`` at all, and the whole first live acceptance ran on
        exactly that. What makes a copy loadable is a transform, and this product
        already has two implementations of it — so the question was which one,
        not what to write.

        **The chosen seam: ``services.agent.state``'s own normalizers, i.e.
        exactly what ``ArtifactManager.clone()`` does to a working copy.**
        The alternative was ``simulation_network_builder._make_network_standalone``,
        and it is rejected for a measured reason, not a stylistic one:

        * ``_make_network_standalone`` also sets ``simRes="2"`` and
          ``useMaxSimSpeed="true"``. Simulation RESOLUTION is time-steps per
          second — it changes vehicle physics and therefore the volumes GEH is
          computed from. The client model declares ``simRes="10"``. A candidate
          run at 2 and graded against an observed side derived from a run at 10
          is a completed measurement with wrong numbers, which is the one thing
          this product exists to refuse.
        * It is reached through ``build_scenario_tree(source_inpx, scenario_id)``,
          which resolves a SCENARIO VARIANT. The agent run path resolves none:
          MEASURED, the working copy that scored this workspace's baseline is
          the raw upload plus two attribute flips (1,391,686 B -> 1,391,687 B,
          the single byte of ``"true"`` -> ``"false"``). Searching a variant the
          scoring run never ran would optimise against a different network than
          the one the workbook grades.
        * COMPARABILITY is the whole point. The baseline candidate must
          reproduce the scoring run's GEH, because the gate, the ledger's
          baseline row and the holdout all rest on it. The scoring run's source
          was an ``ArtifactManager`` working copy (``routers/assistant.py`` sets
          ``tools_run.set_active_run_source(artifact_manager.working_path)``), so
          the candidate materialization must be that same transform and no other.

        The three functions are called directly rather than through
        ``ArtifactManager.clone()`` because that method is exactly
        ``copyfile + these three + _refresh_shadow_model()``, and the shadow-model
        parse is a full re-parse of a 1.4 MB XML that NOTHING here reads —
        charged, per candidate, to the search's own wall-clock budget, which IS
        its candidate count. ``test_the_materialization_matches_artifactmanager_clone``
        pins the file effects against a real ``clone()`` so the two cannot drift.

        One deliberate difference from ``clone()``: the candidate keeps the
        SOURCE'S OWN STEM instead of being renamed ``working.inpx``. A ``.layx``
        sibling is auto-loaded by Vissim on a same-directory-same-stem match
        (``_copy_sibling_assets``' own docstring says so), so preserving the stem
        is what keeps that convention working here.

        Returns ``(path, None)`` or ``(path, reason)``. A full disk, a read-only
        work dir and a work dir that is really a file are all ordinary operating
        conditions on a consultant's machine, and none of them is a reason to
        throw away the candidates a search has already measured.
        """
        # Imported here, not at module scope: ``services.agent.state`` pulls in
        # the shadow-model parser, and this module is imported by
        # ``parameter_search.runner_requirement()`` on every rail read.
        from services.agent.state import (
            _copy_sibling_assets,
            _disable_scenario_management_flag,
            _force_single_run,
        )

        run_dir = self._candidate_dir(index, seed)
        run_inpx = run_dir / source.name
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._clear_stale_results(run_dir)
            shutil.copyfile(source, run_inpx)
            # The .inpx's XML references .vap/.pua/.prbc siblings by exact
            # relative filename, so a network with external signal-controller
            # programs does not merely look different without them — it fails to
            # init. The scoring run's working copy had them; the candidate must.
            _copy_sibling_assets(source, run_dir)
            _disable_scenario_management_flag(run_inpx)
            # Belt to the COM braces in ``_pin_single_run``: an export carrying
            # numRuns > 1 writes N databases for ONE run and the readers SUM them.
            _force_single_run(run_inpx)
        except OSError as exc:
            logger.warning(
                "parameter search candidate %d could not be materialized into %s (%s: %s)",
                index,
                run_dir,
                type(exc).__name__,
                exc,
            )
            return run_inpx, (
                f"{CANDIDATE_WORKSPACE_FAILED}: could not materialize the network into {run_dir} "
                f"({type(exc).__name__}: {exc})"
            )

        # READ IT BACK. The same discipline every parameter write in this file
        # follows, for the same reason: the normalizer above is best-effort by
        # design (an unreadable file leaves the copy as-is and returns False), and
        # a candidate that is still scenario-managed cannot be loaded. Learning
        # that from its own bytes costs a file read; learning it from COM costs a
        # Vissim start-up per candidate and returns an error that names nothing.
        blocker = self._standalone_blocker(run_inpx)
        if blocker is not None:
            return run_inpx, blocker
        return run_inpx, None

    @staticmethod
    def _standalone_blocker(run_inpx: Path) -> str | None:
        """``None`` when this file is loadable, a named reason when it is not.

        Reads the ONE state that is known to make ``LoadNet`` refuse a file whose
        XML is otherwise fine: ``<netPara underScenMngm="true">``. That root cause
        is not this module's finding — ``services/agent/state.py``'s module
        comment records the whole live investigation, including the ruled-out
        version hypothesis and the byte-identical-otherwise copy that loaded once
        the flag was flipped. This asserts the outcome of applying that fix.

        The pattern is ``state``'s OWN compiled regex, not a second one spelled
        here: a check that looked for a different shape than the fix writes is a
        check that can pass over an unfixed file.
        """
        from services.agent.state import _UNDER_SCEN_MNGM_TRUE_RE

        try:
            text = run_inpx.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return (
                f"{CANDIDATE_NOT_STANDALONE}: the materialized candidate at {run_inpx} could not "
                f"be read back ({type(exc).__name__}: {exc})"
            )
        if _UNDER_SCEN_MNGM_TRUE_RE.search(text):
            return (
                f"{CANDIDATE_NOT_STANDALONE}: {run_inpx.name} still declares "
                'netPara/@underScenMngm="true", so Vissim would refuse to load it '
                "(the scenario-managed export needs the flag flipped in the copy — see "
                "services/agent/state.py's module comment for the measured root cause)"
            )
        return None

    def _read_behaviour(self, ctrl: Any) -> dict[str, Any]:
        """The target behaviour as Vissim holds it RIGHT NOW, or ``{}``.

        One extra COM read per candidate — cheap next to the Vissim start-up it
        rides along with, and it is what makes :func:`apply_order`'s decision a
        measurement of the model rather than an assumption about it. ``{}`` on
        any failure: the order then falls back to the deterministic sorted one,
        and a write that consequently fails is still caught by the read-back and
        named. Never raises, because a housekeeping read must not decide the fate
        of a candidate the run itself could have scored.
        """
        try:
            behaviour = ctrl.get_driving_behavior(int(self.behavior_no))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "parameter search: could not read behaviour %s before applying (%s: %s) — "
                "falling back to the sorted write order",
                self.behavior_no,
                type(exc).__name__,
                exc,
            )
            return {}
        return dict(behaviour or {})

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
        # THE ORDER IS PART OF THE WRITE. Vissim enforces a coupling between two
        # of these fields at SET time, on the model as it stands mid-application,
        # so a feasible target can be refused purely for being written in the
        # wrong sequence — measured 2026-08-21, 12 of 13 live candidates.
        # ``apply_order`` derives a sequence whose every intermediate state is
        # legal, from the behaviour as Vissim holds it right now; see its algebra.
        current = self._read_behaviour(ctrl)
        values = dict(vector)
        for parameter in apply_order(values, current):
            target = float(values[parameter])
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
            # NAME THE FIRST SUSPECT. Vissim writes a run's database beside the
            # loaded .inpx as ``<stem>.results/``, which is inside this directory
            # — PROVEN on the real client model, whose export declares
            # ``evaluation/@evalOutDir="#examples#"`` and whose baseline run
            # nevertheless landed at ``seed_00/working.results/1.db``, a sibling
            # of ``seed_00/working.inpx`` (that placeholder does not redirect,
            # and ``configure_network_performance_evaluation`` sets no output
            # directory either). A file carrying an ABSOLUTE evalOutDir would
            # redirect, and the failure would look exactly like this line — so
            # the line says where to look instead of leaving a mystery.
            return (
                f"{RUN_DID_NOT_COMPLETE}: RunContinuous wrote no result database under {run_dir}. "
                "If this network's evaluation/@evalOutDir names an absolute path, the results "
                "went there instead of beside the candidate .inpx."
            )
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
    "PARAMETER_COUPLINGS",
    "VECTOR_INFEASIBLE_COUPLING",
    "VISSIM_EXIT_GRACE_S",
    "CANDIDATE_NOT_STANDALONE",
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
    "FieldCoupling",
    "ScoringContext",
    "VissimSearchRunner",
    "apply_order",
    "geh_by_node",
    "infeasible_coupling",
    "report_refusal",
    "runner_availability",
    "runner_available",
    "terminate_pids",
    "vissim_pids",
]

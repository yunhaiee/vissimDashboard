"""RUN-PROGRESS UI (2026-07-28): tests for the seed-boundary progress
bookkeeping added to both simulation job paths in routers/simulations.py --
``_run_simulation`` (interactive single-seed, RunSingleStep) and
``_run_agent_simulation_job`` (agent multi-seed, RunContinuous) -- plus the
GET job-status endpoint's exposure of the new fields and the thread-safety
of concurrent reads against the worker thread's writes.

No real Vissim COM anywhere in this file. Fakes write real files to disk
(a result .db with a SIMULATIONRUN row) so the job runners' own
verification logic (never trust "done" without that row) stays exercised
exactly as it is in production.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import services.simulation_runs as simulation_runs
import services.vissim_com as vissim_com
from services import workspaces
from routers import simulations as simulations_router


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "registry"
    monkeypatch.setattr(simulation_runs, "SIMULATION_ROOT", root)
    monkeypatch.setattr(simulation_runs, "REGISTRY_PATH", root / "simulation_runs.json")
    return root


@pytest.fixture(autouse=True)
def _clear_in_memory_jobs():
    """``routers.simulations._jobs`` is a process-global dict -- clear it
    before and after each test so a leftover job_id from a previous test
    (or a previous run of this same test id) never leaks in."""
    simulations_router._jobs.clear()
    yield
    simulations_router._jobs.clear()


def _spy_update_job(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Records every ``updates`` kwargs dict passed to ``_update_job``, in
    call order, while still delegating to the real implementation -- the
    only way to observe the exact sequence of seed-boundary/phase-transition
    updates a job runner issues without re-deriving it from job-record
    snapshots (which would only show the LATEST merged state, not the
    sequence)."""
    calls: list[dict[str, Any]] = []
    real_update_job = simulations_router._update_job

    def spy(job_id: str, **updates: Any):
        calls.append(dict(updates))
        return real_update_job(job_id, **updates)

    monkeypatch.setattr(simulations_router, "_update_job", spy)
    return calls


# ---------------------------------------------------------------------------
# Fake multi-seed VissimController -- writes a real result .db per seed,
# like a real RunContinuous call's side effects on disk.
# ---------------------------------------------------------------------------


class _FakeMultiSeedController:
    def __init__(self) -> None:
        self._dir: Path | None = None

    def connect(self, **kwargs: Any) -> None:
        pass

    def load_network(self, inpx_path: str) -> None:
        self._dir = Path(inpx_path).parent

    def set_sim_duration(self, seconds: int) -> None:
        pass

    def set_random_seed(self, seed: int) -> None:
        pass

    def configure_network_performance_evaluation(self, *, duration_s: int) -> dict[str, Any]:
        return {"vehicle_network_performance": {"collect_data": True}}

    def configure_vehicle_record_evaluation(self, *, duration_s: int, resolution: int = 5) -> dict[str, Any]:
        raise RuntimeError("trajectory recording not exercised by this test file")

    def disconnect(self) -> None:
        pass

    @property
    def vissim(self):
        return self

    @property
    def Simulation(self):
        return self

    def RunContinuous(self) -> None:
        assert self._dir is not None
        db_path = self._dir / "run.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER)")
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42)")
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# _run_agent_simulation_job (multi-seed, RunContinuous) -- the path this
# milestone's live-demo bug is actually about (10+ minutes, zero indication).
# ---------------------------------------------------------------------------


def test_agent_multi_seed_progress_sequence_is_monotonic_and_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    monkeypatch.delenv("AGENT_RUN_TRAJECTORY_MAX_S", raising=False)
    monkeypatch.setattr(vissim_com, "VissimController", lambda: _FakeMultiSeedController())

    source = tmp_path / "source.inpx"
    source.write_text("<net/>", encoding="utf-8")

    job_id = "progtest01"
    seeds = [11, 22, 33]
    simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": 60, "random_seeds": seeds}
    )
    # eta_s/completed_seeds/phase all start at their honest defaults BEFORE
    # the worker thread has run at all (create_job_record's own contract).
    fresh = simulation_runs.get_job_record(job_id)
    assert fresh is not None
    assert fresh["total_seeds"] == 3
    assert fresh["completed_seeds"] == 0
    assert fresh["phase"] == "queued"
    assert fresh["eta_s"] is None

    calls = _spy_update_job(monkeypatch)

    # Duration guard skips trajectory arming entirely (>1800s default cap is
    # not hit here since it's env-based; use a long AGENT_RUN_TRAJECTORY_MAX_S
    # override of 0 so configure_vehicle_record_evaluation -- which this
    # fake deliberately raises on -- is never even attempted).
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_MAX_S", "0")

    simulations_router._run_agent_simulation_job(job_id, source, 60, seeds)

    record = simulation_runs.get_job_record(job_id)
    assert record is not None
    assert record["status"] == "done"
    assert record["phase"] == "done"
    assert record["completed_seeds"] == 3
    assert record["total_seeds"] == 3
    assert record["current_seed"] is None
    assert record["eta_s"] == 0.0
    assert len(record["per_seed_durations"]) == 3
    assert all(isinstance(d, (int, float)) and d >= 0 for d in record["per_seed_durations"])
    assert record["last_update_at"] is not None

    # --- Walk the full call sequence: honesty + monotonicity ---------------
    first_seed_completed_index = None
    completed_seeds_seen: list[int] = []
    current_seed_seen: list[int | None] = []
    for i, updates in enumerate(calls):
        if "completed_seeds" in updates:
            completed_seeds_seen.append(updates["completed_seeds"])
            if updates["completed_seeds"] >= 1 and first_seed_completed_index is None:
                first_seed_completed_index = i
        if "current_seed" in updates:
            current_seed_seen.append(updates["current_seed"])

    # completed_seeds is monotonically non-decreasing across the whole call
    # sequence (0, 0, 1, 2, 3 -- never a regression).
    assert completed_seeds_seen == sorted(completed_seeds_seen)
    assert completed_seeds_seen[-1] == 3

    # current_seed visits 1, 2, 3 (arming) and None (boundary completions) --
    # in particular it must reach every seed index, never skip one.
    assert [v for v in current_seed_seen if v is not None] == [1, 2, 3]

    # Honesty: eta_s is None on every update BEFORE the first seed's own
    # completion update, and a real (non-None, non-negative) number on every
    # update FROM that point on that carries eta_s at all.
    assert first_seed_completed_index is not None
    for i, updates in enumerate(calls):
        if "eta_s" not in updates:
            continue
        if i < first_seed_completed_index:
            assert updates["eta_s"] is None, f"call {i} guessed eta_s before any seed completed: {updates}"
        else:
            assert updates["eta_s"] is not None
            assert updates["eta_s"] >= 0

    # Phase transitions observed, in the right relative order per seed:
    # arming -> running -> collecting, three times (once per seed), the
    # overall job start also stamped "arming".
    phases = [u["phase"] for u in calls if "phase" in u]
    assert phases[0] == "arming"  # overall job-start bookkeeping
    assert phases.count("arming") == 4  # job-start + once per seed
    assert phases.count("running") == 3
    assert phases.count("collecting") == 3
    assert phases[-1] == "done"

    # Never SimBreakAt-chunking / per-step sampling: exactly one "running"
    # phase transition per seed, not one per simulated second -- RunContinuous
    # is called exactly once per seed as a single COM call (this fake would
    # otherwise need multiple RunContinuous calls to produce more).
    assert phases.count("running") == len(seeds)


def test_agent_multi_seed_job_error_still_sets_error_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    class _FailingController(_FakeMultiSeedController):
        def configure_network_performance_evaluation(self, *, duration_s: int) -> dict[str, Any]:
            raise RuntimeError("simulated evaluation-config failure")

    monkeypatch.setattr(vissim_com, "VissimController", lambda: _FailingController())

    source = tmp_path / "source.inpx"
    source.write_text("<net/>", encoding="utf-8")
    job_id = "progtest02"
    simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": 60, "random_seeds": [1]}
    )

    simulations_router._run_agent_simulation_job(job_id, source, 60, [1])

    record = simulation_runs.get_job_record(job_id)
    assert record is not None
    assert record["status"] == "error"
    assert record["phase"] == "error"
    assert record["error"]


# ---------------------------------------------------------------------------
# _run_simulation (interactive single-seed, RunSingleStep) -- total_seeds=1,
# eta_s stays None throughout (never a guess -- no seed EVER completes
# before the job itself is terminal for a single-seed job).
# ---------------------------------------------------------------------------


class _FakeInteractiveController:
    def __init__(self, *, step_s: float = 30.0) -> None:
        self._sim_sec = 0.0
        self._step_s = step_s
        self._duration = 0.0

    def connect(self, **kwargs: Any) -> None:
        pass

    def load_network(self, inpx_path: str) -> None:
        pass

    def available_vehicle_input_nos(self) -> list[int]:
        return []

    def available_signal_controller_nos(self) -> list[int]:
        return []

    def set_sim_duration(self, seconds: int) -> None:
        self._duration = float(seconds)

    def set_random_seed(self, seed: int) -> None:
        pass

    def set_vehicle_input(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_signal_cycle(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_signal_offset(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_sim_sec(self) -> float:
        return self._sim_sec

    def run_single_step(self) -> None:
        self._sim_sec += self._step_s

    def is_sim_finished(self) -> bool:
        return self._sim_sec >= self._duration

    def disconnect(self) -> None:
        pass


def test_interactive_single_seed_progress_never_guesses_eta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    monkeypatch.setattr(vissim_com, "VissimController", lambda: _FakeInteractiveController())
    monkeypatch.setattr(
        "services.simulation_network_builder.build_simulation_network",
        lambda **kwargs: kwargs["source_inpx"],
    )

    source = tmp_path / "source.inpx"
    source.write_text("<net/>", encoding="utf-8")
    monkeypatch.setattr(simulations_router, "_simulation_source_inpx", lambda: source)

    job_id = "progtest03"
    simulation_runs.create_job_record(job_id, {"name": "interactive", "random_seed": 42})
    fresh = simulation_runs.get_job_record(job_id)
    assert fresh is not None
    assert fresh["total_seeds"] == 1

    calls = _spy_update_job(monkeypatch)

    params = simulations_router.SimParams(name="interactive", sim_duration=60, random_seed=42)
    # Unit 1.4: the source .inpx is resolved by the REQUEST handler and
    # handed to the runner frozen (TOCTOU fix), and the runner re-binds the
    # submitting workspace on its own thread.
    simulations_router._run_simulation(
        job_id, params, source, workspaces.current_workspace_id()
    )

    record = simulation_runs.get_job_record(job_id)
    assert record is not None
    assert record["status"] == "done"
    assert record["phase"] == "done"
    assert record["completed_seeds"] == 1
    assert record["current_seed"] is None
    # Single-seed job: eta_s is None for the ENTIRE run (no seed ever
    # completes before the job itself is terminal) -- 0.0 only at the very
    # end, meaning "nothing remains", never an extrapolated guess.
    assert record["eta_s"] == 0.0

    eta_values = [u["eta_s"] for u in calls if "eta_s" in u]
    assert eta_values[:-1] == [None] * (len(eta_values) - 1)
    assert eta_values[-1] == 0.0

    phases = [u["phase"] for u in calls if "phase" in u]
    assert phases == ["arming", "running", "done"]


# ---------------------------------------------------------------------------
# GET job-status endpoint exposes the new fields
# ---------------------------------------------------------------------------


def test_get_simulation_endpoint_exposes_progress_fields(isolated_registry: Path):
    job_id = "progtest04"
    simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": 60, "random_seeds": [1, 2]}
    )

    payload = simulations_router.get_simulation(job_id)

    for field in (
        "total_seeds",
        "completed_seeds",
        "current_seed",
        "phase",
        "per_seed_durations",
        "eta_s",
        "last_update_at",
    ):
        assert field in payload, f"missing field: {field}"
    assert payload["total_seeds"] == 2
    assert payload["completed_seeds"] == 0
    assert payload["phase"] == "queued"
    assert payload["eta_s"] is None


def test_get_simulation_endpoint_reads_the_in_memory_record_when_present(isolated_registry: Path):
    """Once a job is in ``_jobs`` (the worker thread's live view), the GET
    endpoint must reflect THAT state, not a stale on-disk snapshot -- and
    must never hand back a live reference the caller could accidentally
    mutate (see _job_snapshot's own docstring)."""
    job_id = "progtest05"
    record = simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": 60, "random_seeds": [1, 2, 3]}
    )
    simulations_router._jobs[job_id] = record
    simulations_router._update_job(job_id, phase="arming", current_seed=1, completed_seeds=0)

    payload = simulations_router.get_simulation(job_id)
    assert payload["phase"] == "arming"
    assert payload["current_seed"] == 1

    # Mutating the returned payload must never affect the live in-memory
    # record (it's a copy, not an alias) -- otherwise a caller (e.g.
    # FastAPI's response encoder, or a careless future endpoint) could
    # corrupt the worker thread's own bookkeeping.
    payload["phase"] = "corrupted-by-caller"
    assert simulations_router._jobs[job_id]["phase"] == "arming"


# ---------------------------------------------------------------------------
# Thread safety: concurrent _update_job writers + _job_snapshot readers on
# the SAME job_id must never raise (dict-changed-size-during-iteration,
# KeyError, etc.) and every snapshot must carry the full expected key set.
# ---------------------------------------------------------------------------


def test_concurrent_updates_and_reads_never_raise_and_stay_well_formed(isolated_registry: Path):
    job_id = "progtest06"
    record = simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": 60, "random_seeds": [1, 2, 3, 4, 5]}
    )
    simulations_router._jobs[job_id] = record

    expected_keys = set(record.keys())
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer() -> None:
        try:
            for i in range(300):
                simulations_router._update_job(
                    job_id,
                    phase="running" if i % 2 == 0 else "collecting",
                    current_seed=(i % 5) + 1,
                    completed_seeds=i % 5,
                    per_seed_durations=[float(x) for x in range(i % 5)],
                    eta_s=None if i % 5 == 0 else float(i),
                    last_update_at=simulations_router._utc_now_iso(),
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                snapshot = simulations_router._job_snapshot(job_id)
                assert snapshot is not None
                assert expected_keys <= set(snapshot.keys())
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    writer_thread = threading.Thread(target=writer)
    reader_threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in reader_threads:
        t.start()
    writer_thread.start()
    writer_thread.join(timeout=10)
    for t in reader_threads:
        t.join(timeout=10)

    assert not errors, f"concurrent access raised: {errors}"

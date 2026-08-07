"""The agent job runner pins every seed to exactly ONE Vissim run.

MEASURED 2026-08-07 (``runtime/calibration-loop-live-check/artifacts/
FINDING_agent_run_numRuns_blocker.json``, against the real consultant model):
that export carries ``<simulation numRuns="5" randSeed="42"
randSeedIncr="123">``, Vissim's ``RunContinuous`` honours NumRuns, and ONE
``run_simulation(duration_s=900, random_seeds=[42])`` dispatch wrote FIVE
result databases into a single seed directory (seeds 42/165/288/411/534 =
randSeed + randSeedIncr) while the tool envelope still reported
``random_seeds: [42]``. The evidence readers SUM volumes across every .db in
a run dir, so the KPIs came back 5x inflated and ``run_reality_check``
refused outright with ``MULTI_SEED_VOLUMES_NOT_COMPARABLE``.

Two seams close that, and this file owns the second one:

* ``services/agent/state.py``'s ``_force_single_run`` normalizes
  ``numRuns="1"`` into the session working copy at clone time (primary; its
  regressions live in ``tests/test_agent_state.py``);
* ``routers/simulations.py``'s ``_run_agent_simulation_job`` sets NumRuns=1
  over COM with a paired read-back before every ``RunContinuous`` (this
  file) -- unconditional, because the runner accepts ANY source .inpx, not
  only an ArtifactManager working copy.

No real Vissim COM here: ``services.vissim_com.VissimController`` is
monkeypatched to a fake whose ``Simulation`` object records every
``SetAttValue`` and can be told to ignore the write (the "Vissim accepted the
call and did nothing" failure mode this codebase's read-back discipline
exists for) or to raise.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import services.simulation_runs as simulation_runs
import services.vissim_com as vissim_com
from routers import simulations as simulations_router


# ---------------------------------------------------------------------------
# Fake VissimController -- writes a real result .db, records attribute writes
# ---------------------------------------------------------------------------


class _FakeSimulation:
    def __init__(self, controller: "_FakeVissimController") -> None:
        self._controller = controller
        self.att_values: dict[str, Any] = {}

    def SetAttValue(self, name: str, value: Any) -> None:
        self._controller.calls.append(("SetAttValue", name, value))
        if name == "NumRuns" and self._controller.raise_on_num_runs:
            raise RuntimeError("simulated COM failure setting NumRuns")
        if name == "NumRuns" and self._controller.ignore_num_runs:
            # Vissim's silent-failure mode: the call returns cleanly and the
            # value does not move. Exactly what the read-back is for.
            return
        self.att_values[name] = value

    def AttValue(self, name: str) -> Any:
        return self.att_values.get(name)

    def RunContinuous(self) -> None:
        self._controller.calls.append(("RunContinuous", None, None))
        self._controller._run()


class _FakeVissimHandle:
    def __init__(self, controller: "_FakeVissimController") -> None:
        self.Simulation = _FakeSimulation(controller)


class _FakeVissimController:
    #: The network's OWN declared value, as the real consultant export has it.
    #: The fake starts here so a runner that never sets NumRuns leaves 5 in
    #: place and the read-back assertion below genuinely fails.
    NETWORK_DECLARED_NUM_RUNS = 5

    def __init__(self, *, ignore_num_runs: bool = False, raise_on_num_runs: bool = False) -> None:
        self.vissim = _FakeVissimHandle(self)
        self.vissim.Simulation.att_values["NumRuns"] = self.NETWORK_DECLARED_NUM_RUNS
        self.calls: list[tuple[str, Any, Any]] = []
        self.ignore_num_runs = ignore_num_runs
        self.raise_on_num_runs = raise_on_num_runs
        self._dir: Path | None = None

    def connect(self, **kwargs: Any) -> None:
        self.calls.append(("connect", None, None))

    def load_network(self, inpx_path: str) -> None:
        self.calls.append(("load_network", inpx_path, None))
        self._dir = Path(inpx_path).parent

    def set_sim_duration(self, seconds: int) -> None:
        self.calls.append(("set_sim_duration", seconds, None))

    def set_random_seed(self, seed: int) -> None:
        self.calls.append(("set_random_seed", seed, None))

    def configure_network_performance_evaluation(self, *, duration_s: int) -> dict[str, Any]:
        self.calls.append(("configure_evaluation", duration_s, None))
        return {"vehicle_network_performance": {"collect_data": True}}

    def configure_vehicle_record_evaluation(
        self, *, duration_s: int, resolution: int = 5
    ) -> dict[str, Any]:
        raise RuntimeError("trajectory recording not exercised by this file")

    def disconnect(self) -> None:
        self.calls.append(("disconnect", None, None))

    def _run(self) -> None:
        assert self._dir is not None
        conn = sqlite3.connect(str(self._dir / "run.db"))
        conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER)")
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42)")
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "registry"
    monkeypatch.setattr(simulation_runs, "SIMULATION_ROOT", root)
    monkeypatch.setattr(simulation_runs, "REGISTRY_PATH", root / "simulation_runs.json")
    return root


@pytest.fixture(autouse=True)
def _clear_in_memory_jobs():
    simulations_router._jobs.clear()
    yield
    simulations_router._jobs.clear()


def _run_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seeds: list,
    ignore_num_runs: bool = False,
    raise_on_num_runs: bool = False,
) -> tuple[dict[str, Any], list[_FakeVissimController]]:
    # AGENT_RUN_TRAJECTORY_MAX_S=0 keeps the (deliberately raising) vehicle-
    # record arming out of the way -- this file is about NumRuns only.
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_MAX_S", "0")

    created: list[_FakeVissimController] = []

    def factory() -> _FakeVissimController:
        fake = _FakeVissimController(
            ignore_num_runs=ignore_num_runs, raise_on_num_runs=raise_on_num_runs
        )
        created.append(fake)
        return fake

    monkeypatch.setattr(vissim_com, "VissimController", factory)

    source = tmp_path / "source.inpx"
    source.write_text("<net/>", encoding="utf-8")

    job_id = "numrunstest"
    simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": 60, "random_seeds": seeds}
    )
    simulations_router._run_agent_simulation_job(job_id, source, 60, seeds)

    record = simulation_runs.get_job_record(job_id)
    assert record is not None
    return record, created


def _num_runs_writes(controller: _FakeVissimController) -> list[Any]:
    return [
        value
        for call, name, value in controller.calls
        if call == "SetAttValue" and name == "NumRuns"
    ]


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_every_seed_is_pinned_to_a_single_run_before_run_continuous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    """Three seeds, three fresh controllers, three NumRuns=1 writes -- one
    per loaded network, since each seed gets its own load_network (an
    attribute set on a previous seed's instance would not carry)."""
    record, created = _run_job(tmp_path, monkeypatch, seeds=[11, 22, 33])

    assert record["status"] == "done", record.get("error")
    assert len(created) == 3
    for controller in created:
        assert _num_runs_writes(controller) == [1]
        assert controller.vissim.Simulation.AttValue("NumRuns") == 1


def test_num_runs_is_set_after_load_network_and_before_run_continuous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    """Ordering is load-bearing: an attribute written before ``LoadNet``
    belongs to whatever network was loaded before (docs/phase2/
    00-EXECUTION-GUIDE.md SS3.1's not-retroactive rule), and one written
    after ``RunContinuous`` is too late by an entire run."""
    _record, created = _run_job(tmp_path, monkeypatch, seeds=[42])

    sequence = [call for call, _name, _value in created[0].calls]
    num_runs_index = next(
        i
        for i, (call, name, _value) in enumerate(created[0].calls)
        if call == "SetAttValue" and name == "NumRuns"
    )
    assert sequence.index("load_network") < num_runs_index
    assert num_runs_index < sequence.index("RunContinuous")


def test_a_none_seed_still_gets_num_runs_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    """``random_seeds=[None]`` means "keep the network's own RandSeed" -- it
    does NOT mean "keep the network's own NumRuns". This is the default
    single-seed agent run, and the shape the finding was measured on."""
    record, created = _run_job(tmp_path, monkeypatch, seeds=[None])

    assert record["status"] == "done", record.get("error")
    assert _num_runs_writes(created[0]) == [1]
    assert ("set_random_seed", None, None) not in created[0].calls


def test_a_silently_ignored_num_runs_write_fails_the_job_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    """Vissim fails silently, so the read-back is the verification (docs/
    phase2/00-EXECUTION-GUIDE.md SS4). A NumRuns that does not move must
    refuse the run outright: five result databases summed into one set of
    KPIs is wrong numbers, which is strictly worse than no numbers."""
    record, created = _run_job(tmp_path, monkeypatch, seeds=[42], ignore_num_runs=True)

    assert record["status"] == "error"
    assert "NumRuns" in record["error"]
    assert "5" in record["error"]
    # Refused BEFORE the run -- never a completed run with wrong multiplicity.
    assert ("RunContinuous", None, None) not in created[0].calls
    assert ("configure_evaluation", 60, None) not in created[0].calls
    # ...and the COM instance is still released on the way out.
    assert ("disconnect", None, None) in created[0].calls


def test_a_raising_num_runs_write_fails_the_job_with_a_named_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_registry: Path
):
    """A COM error on the set itself is wrapped rather than left to surface
    as a bare, unattributable com_error in the job record."""
    record, created = _run_job(tmp_path, monkeypatch, seeds=[42], raise_on_num_runs=True)

    assert record["status"] == "error"
    assert "NumRuns" in record["error"]
    assert ("RunContinuous", None, None) not in created[0].calls

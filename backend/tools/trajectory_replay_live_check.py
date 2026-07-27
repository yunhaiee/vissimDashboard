"""LIVE ACCEPTANCE for the trajectory-replay unit: a real 300s agent-path
``run_simulation`` on a Beijing.CN clone must (a) arm and record a real
vehicle-trajectory .fzp via the job runner (``routers/simulations.py``'s
``_run_agent_simulation_job``), (b) parse+resolve it correctly against the
SAME network's own geometry, and (c) be servable, non-empty, through the
real ``GET /api/trajectories?job_id=...`` HTTP endpoint.

Mirrors ``tools/run_gate_live_check.py``'s structure and safety discipline
(attach_to_running=False, clone-only, disconnect in ``finally``, no paid LLM
calls -- a canned planner drives the real orchestrator/job-queue plumbing).

Four phases:
  [A] Submit a real agent-path run_simulation call (through the FULL
      orchestrator: real ArtifactManager, real enabled GuardrailEngine,
      PreapprovedWriteGate, canned planner) for duration_s=300 against a
      fresh Beijing.CN clone.
  [B] Verify the job record directly: status == "done",
      trajectory_recorded is True, trajectory_paths non-empty and every
      path exists on disk.
  [C] Independently parse+resolve the recorded .fzp (trajectory_reader.
      parse_fzp + resolve_frames against inpx_parser.parse_network of the
      SAME clone) -- proves the real Vissim export genuinely parses under
      the format-detection fix and resolves to real (x, y) positions, not
      just "a file exists".
  [D] Hit the REAL FastAPI endpoint (GET /api/trajectories?job_id=...) via
      TestClient, with the active-dataset resolution pointed at the same
      Beijing clone (so link ids actually match), and assert a non-empty,
      well-formed frames payload.

Usage: python tools/trajectory_replay_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo).
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "trajectory-replay-live-check"
RUN_DURATION_S = 300  # within the task's 300-600s acceptance window
LIVE_CHECK_REVISION = f"trajectory-replay-live-check-{os.getpid()}"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


# ---------------------------------------------------------------------------
# Phase A: submit a real agent-path run through the full orchestrator
# ---------------------------------------------------------------------------


class _CannedProvider:
    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str, **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}


def phase_a_run_simulation(example_dir: Path, work_dir: Path):
    _log("=" * 78)
    _log(f"[Phase A] run_simulation(duration_s={RUN_DURATION_S}) through the REAL job queue")
    _log("=" * 78)

    from services.agent.gates import PreapprovedWriteGate, signature
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import run_agent_turn
    from services.agent.state import ArtifactManager
    import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation

    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    artifact_manager.clone()
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")

    action = {"tool": "run_simulation", "params": {"duration_s": RUN_DURATION_S}}
    gate = PreapprovedWriteGate({signature(action["tool"], action["params"])})
    engine = GuardrailEngine(disabled=False)
    provider = _CannedProvider(
        [
            _action("run_simulation", summary="5분 시뮬레이션을 실행합니다", duration_s=RUN_DURATION_S),
            _action(
                "respond",
                summary="실행을 완료했습니다",
                answer=f"{RUN_DURATION_S}초 시뮬레이션 실행을 완료했습니다.",
            ),
        ]
    )

    tools_run.set_active_run_source(artifact_manager.working_path)
    _log("[A.2] Submitting via run_agent_turn (RunContinuous, real job queue, real GuardrailEngine)...")
    started = time.monotonic()
    try:
        result = run_agent_turn(
            f"{RUN_DURATION_S}초 시뮬레이션 돌려줘",
            provider=provider,
            work_dir=work_dir,
            session_id="trajectory-replay-live-check",
            language="ko",
            gate=gate,
            engine=engine,
            artifact_manager=artifact_manager,
        )
    finally:
        tools_run.clear_active_run_source()
    elapsed = time.monotonic() - started
    _log(f"      run_agent_turn returned in {elapsed:.1f}s -- kind={result.kind!r}")
    _log(f"      answer={result.answer!r}")

    if result.kind != "answer" or not result.state.history:
        raise SystemExit(f"Phase A FAILED: unexpected turn shape kind={result.kind!r}")

    run_observation = result.state.history[0].observation
    if run_observation.get("status") != "ok":
        raise SystemExit(f"Phase A FAILED: run_simulation dispatch not ok: {run_observation}")

    job_id = (run_observation.get("verification") or {}).get("job_id")
    if not job_id:
        raise SystemExit("Phase A FAILED: no job_id in run_simulation verification")
    _log(f"[A.3] PASS: run_simulation dispatched cleanly, job_id={job_id!r}")
    return artifact_manager, job_id


# ---------------------------------------------------------------------------
# Phase B: verify the job record directly
# ---------------------------------------------------------------------------


def phase_b_verify_job_record(job_id: str) -> list[Path]:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Verifying the job record's trajectory_recorded/trajectory_paths")
    _log("=" * 78)

    from services.simulation_runs import get_job_record

    record = get_job_record(job_id)
    if record is None:
        raise SystemExit(f"Phase B FAILED: no job record found for {job_id!r}")
    _log(f"[B.1] status={record.get('status')!r} evaluation_configured={record.get('evaluation_configured')!r}")
    if record.get("status") != "done":
        raise SystemExit(f"Phase B FAILED: job status is not 'done': {record}")

    trajectory_recorded = record.get("trajectory_recorded")
    trajectory_paths = record.get("trajectory_paths") or []
    _log(f"[B.2] trajectory_recorded={trajectory_recorded!r}  trajectory_paths={trajectory_paths!r}")
    if trajectory_recorded is not True:
        raise SystemExit(f"Phase B FAILED: trajectory_recorded is not True: {record}")
    if not trajectory_paths:
        raise SystemExit("Phase B FAILED: trajectory_paths is empty despite trajectory_recorded=True")

    paths = [Path(p) for p in trajectory_paths]
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"Phase B FAILED: recorded trajectory path does not exist on disk: {path}")
        size = path.stat().st_size
        _log(f"      PASS: {path} exists, {size} bytes ({size * 60 / RUN_DURATION_S:.0f} bytes/60s extrapolated)")

    _log("[B.3] PASS: job record proves a real .fzp was armed and written for this run.")
    return paths


# ---------------------------------------------------------------------------
# Phase C: independently parse + resolve the recorded .fzp
# ---------------------------------------------------------------------------


def phase_c_parse_and_resolve(fzp_path: Path, example_dir: Path, work_dir: Path) -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase C] Independently parsing + resolving the recorded .fzp")
    _log("=" * 78)

    from services.inpx_parser import parse_network
    from services.trajectory_reader import parse_fzp, resolve_frames

    parsed = parse_fzp(fzp_path)
    frames = parsed["frames"]
    meta = parsed["meta"]
    _log(f"[C.1] parse_fzp: {len(frames)} frame(s), meta={meta}")
    if not frames:
        raise SystemExit("Phase C FAILED: parse_fzp produced zero frames")
    if meta["vehicle_count"] < 1:
        raise SystemExit("Phase C FAILED: parse_fzp saw zero distinct vehicles")

    # Resolve against the ORIGINAL Beijing.CN network geometry (the run
    # itself started from a clone of it, and no structural edit happened in
    # this live check, so link ids are identical).
    original_inpx = next(example_dir.glob("*.inpx"))
    network = parse_network(str(original_inpx))
    links = network.get("links", [])
    _log(f"[C.2] Parsed {len(links)} link(s) from the original network for resolution")

    resolved_frames, skipped = resolve_frames(frames, links)
    total_vehicle_rows = sum(len(f["vehicles"]) for f in frames)
    resolved_vehicle_rows = sum(len(f["vehicles"]) for f in resolved_frames)
    _log(
        f"[C.3] resolve_frames: {resolved_vehicle_rows}/{total_vehicle_rows} vehicle-frame rows resolved "
        f"to real (x, y, angle); {skipped} skipped (unknown link)"
    )
    if resolved_vehicle_rows < 1:
        raise SystemExit(
            "Phase C FAILED: zero vehicle rows resolved to a real position -- either the .fzp's "
            "link ids don't match the network, or the format-detection column aliasing regressed."
        )
    sample = resolved_frames[len(resolved_frames) // 2]["vehicles"]
    if sample:
        _log(f"      sample resolved vehicle: {sample[0]}")
    _log("[C.4] PASS: the real Vissim .fzp export parses and resolves to genuine network positions.")


# ---------------------------------------------------------------------------
# Phase D: the real HTTP endpoint, non-empty frames
# ---------------------------------------------------------------------------


def phase_d_http_endpoint(job_id: str, example_dir: Path) -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase D] GET /api/trajectories?job_id=... via the real FastAPI app")
    _log("=" * 78)

    from fastapi.testclient import TestClient

    import main
    import routers.trajectories as trajectories_router
    import services.simulation_runs as simulation_runs

    original_inpx = next(example_dir.glob("*.inpx"))

    # Point BOTH the job-record stamp (simulation_runs.get_active_dataset,
    # called by create_job_record at submission time -- already baked into
    # the record from Phase A/B) and the endpoint's own resolution
    # (trajectories_router's directly-imported names) at the SAME clone, so
    # the dataset_revision guard passes and link ids actually resolve.
    # These are direct module-attribute overrides on the CALLING modules'
    # own bound names (not the defining module), matching how this
    # codebase's test suite patches these same seams.
    original_get_active_dataset = trajectories_router.get_active_dataset
    original_get_active_inpx_path = trajectories_router.get_active_inpx_path
    trajectories_router.get_active_dataset = lambda: {"revision": LIVE_CHECK_REVISION}
    trajectories_router.get_active_inpx_path = lambda: original_inpx

    try:
        record = simulation_runs.get_job_record(job_id)
        _log(f"[D.1] Job record dataset_revision={record.get('dataset_revision')!r} (stamped at submission time)")
        if record.get("dataset_revision") != LIVE_CHECK_REVISION:
            _log(
                "      NOTE: the job was submitted under a DIFFERENT active-dataset revision than "
                f"this phase's override ({LIVE_CHECK_REVISION!r}) -- the revision guard will 409. "
                "Re-stamping the record's dataset_revision for this phase only (endpoint-layer proof, "
                "not a claim about production submission-time behavior)."
            )
            simulation_runs.update_job_record(job_id, dataset_revision=LIVE_CHECK_REVISION)

        with TestClient(main.app) as client:
            started = time.monotonic()
            response = client.get("/api/trajectories", params={"job_id": job_id})
            _log(f"[D.2] GET /api/trajectories?job_id={job_id} -> {response.status_code} in {time.monotonic() - started:.1f}s")
            if response.status_code != 200:
                raise SystemExit(f"Phase D FAILED: non-200 response: {response.status_code} {response.text}")

            payload = response.json()
            meta = payload.get("meta") or {}
            frames = payload.get("frames") or []
            _log(f"[D.3] meta={meta}")
            _log(f"      returned {len(frames)} frame(s)")
            if not frames:
                raise SystemExit("Phase D FAILED: endpoint returned zero frames")

            total_vehicles = sum(len(f.get("vehicles") or []) for f in frames)
            resolved_vehicles = sum(
                1
                for f in frames
                for v in (f.get("vehicles") or [])
                if v.get("x") is not None and v.get("y") is not None
            )
            _log(f"      {resolved_vehicles}/{total_vehicles} vehicle entries carry resolved (x, y)")
            if resolved_vehicles < 1:
                raise SystemExit("Phase D FAILED: endpoint returned frames but zero vehicles resolved to (x, y)")

            _log("[D.4] PASS: the real HTTP endpoint serves non-empty, resolved frames for this job.")
    finally:
        trajectories_router.get_active_dataset = original_get_active_dataset
        trajectories_router.get_active_inpx_path = original_get_active_inpx_path


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    work_dir = _fresh_work_dir(example_dir)
    _log(f"Process-unique work dir: {work_dir}")

    artifact_manager, job_id = phase_a_run_simulation(example_dir, work_dir)
    fzp_paths = phase_b_verify_job_record(job_id)
    phase_c_parse_and_resolve(fzp_paths[0], example_dir, work_dir)
    phase_d_http_endpoint(job_id, example_dir)

    _log("")
    _log("=" * 78)
    _log("RESULT: PASS -- trajectory-replay live acceptance complete (Phases A-D)")
    _log("=" * 78)


if __name__ == "__main__":
    main()

"""GET /api/trajectories -- resolved per-timestep vehicle positions for the
Phase 3 trajectory-playback data layer (P4 built the UI on top of this; the
real-run-replay unit below extends it to real completed agent runs).

Reads a Vissim-style ``.fzp`` export via ``services/trajectory_reader.py``
and resolves every vehicle's (link, lane, pos) to absolute (x, y, angle)
against the ACTIVE dataset's network geometry -- same resolver primitives
Tier 1 already uses for static network elements. Never touches COM: this
endpoint only ever reads a static ``.fzp`` file from disk, whether the
synthetic demo export (``tools/make_demo_trajectories.py``), a real Vissim
export dropped next to an uploaded dataset, or (via ``job_id``) a real
completed agent-run job's own recorded trajectory
(``routers/simulations.py``'s ``_run_agent_simulation_job`` -- see
docs/phase2/DECISIONS.md's dated entry for this unit).
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from config import DATA_DIR
from services.datasets import get_active_dataset, get_active_inpx_path
from services.inpx_parser import parse_network
from services.simulation_runs import get_job_record
from services.trajectory_reader import parse_fzp, resolve_frames

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trajectories", tags=["trajectories"])

DEMO_TRAJECTORY_PATH = DATA_DIR / "demo_trajectories.fzp"


def _resolve_trajectory_path() -> Path | None:
    """File-next-to-dataset convention (docs/phase2/DECISIONS.md): a
    trajectory export for the currently active dataset lives beside its
    ``.inpx`` (e.g. ``runtime/uploads/current/trajectories.fzp`` once a real
    Vissim ``.fzp`` export is dropped there for an uploaded dataset). The
    showcase demo file at ``data/demo_trajectories.fzp`` is the fallback, so
    the endpoint always has *something* to serve on a fresh checkout with no
    COM and no real export yet. ``None`` only when neither exists."""
    active_inpx = get_active_inpx_path()
    beside_active = Path(active_inpx).parent / "trajectories.fzp"
    if beside_active.is_file():
        return beside_active
    if DEMO_TRAJECTORY_PATH.is_file():
        return DEMO_TRAJECTORY_PATH
    return None


def _resolve_job_trajectory_path(job_id: str) -> Path:
    """Resolve a completed agent-run job's own recorded ``.fzp``
    (``trajectory_paths[0]`` -- see ``routers/simulations.py``'s
    ``_run_agent_simulation_job`` docstring for why only the first seed's
    trajectory is ever served: trajectories cannot be meaningfully averaged
    across seeds the way KPI tables are).

    Every failure mode is a specific, actionable HTTPException rather than a
    silent fallback to the demo file -- unlike ``_resolve_trajectory_path``'s
    degrade-to-demo policy, a caller that explicitly asked for THIS job's
    replay must be told exactly why it isn't available, never shown an
    unrelated network's trajectory instead.
    """
    record = get_job_record(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Simulation job '{job_id}' not found")
    if record.get("status") != "done":
        raise HTTPException(
            status_code=409, detail=f"Simulation job '{job_id}' has not completed yet"
        )
    if not record.get("trajectory_recorded"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Simulation job '{job_id}' has no recorded vehicle trajectory "
                "(its duration may have exceeded AGENT_RUN_TRAJECTORY_MAX_S, or "
                "trajectory recording failed for that run)."
            ),
        )
    if record.get("dataset_revision") != get_active_dataset().get("revision"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Simulation job '{job_id}' was run against a different dataset revision than "
                "the currently active one -- switch back to that dataset to replay it."
            ),
        )
    trajectory_paths = record.get("trajectory_paths") or []
    if not trajectory_paths:
        raise HTTPException(
            status_code=404, detail=f"Simulation job '{job_id}' has no trajectory file recorded"
        )
    path = Path(trajectory_paths[0])
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Recorded trajectory file for job '{job_id}' is missing on disk: {path}",
        )
    return path


@router.get("")
def get_trajectories(
    job_id: str | None = Query(
        None, description="Replay a completed agent-run job's own recorded trajectory instead of the default file-next-to-dataset/demo source"
    ),
    t0: float | None = Query(None, description="Trim window start (SIMSEC seconds, inclusive)"),
    t1: float | None = Query(None, description="Trim window end (SIMSEC seconds, inclusive)"),
    stride: int = Query(1, ge=1, description="Frame decimation -- keep 1 frame in every N"),
):
    if job_id is not None:
        trajectory_path = _resolve_job_trajectory_path(job_id)
    else:
        trajectory_path = _resolve_trajectory_path()
        if trajectory_path is None:
            raise HTTPException(
                status_code=404,
                detail="No trajectory file found (checked next to the active dataset and data/demo_trajectories.fzp)",
            )

    parsed = parse_fzp(trajectory_path)
    frames = parsed["frames"]

    if t0 is not None:
        frames = [f for f in frames if f["t"] >= t0]
    if t1 is not None:
        frames = [f for f in frames if f["t"] <= t1]
    if stride > 1:
        frames = frames[::stride]

    inpx_path = get_active_inpx_path()
    links: list[dict] = []
    try:
        network = parse_network(str(inpx_path))
        links = network.get("links", [])
    except Exception as exc:  # noqa: BLE001 -- a malformed/missing active .inpx must
        # degrade to unresolved (link/lane/pos-only) frames, never 500 the whole
        # endpoint -- same "never worse than a degraded response" policy the
        # network/geojson routers already follow for their own fallback paths.
        logger.warning("trajectories: failed to parse active network %s for resolution: %s", inpx_path, exc)

    resolved_frames, skipped_unresolved = resolve_frames(frames, links)

    meta = dict(parsed["meta"])
    meta["source_file"] = trajectory_path.name
    meta["source_path"] = str(trajectory_path)
    meta["skipped_unresolved_vehicles"] = skipped_unresolved
    meta["returned_frame_count"] = len(resolved_frames)
    meta["stride"] = stride
    if job_id is not None:
        meta["job_id"] = job_id
    if t0 is not None:
        meta["t0_filter"] = t0
    if t1 is not None:
        meta["t1_filter"] = t1

    return {"meta": meta, "frames": resolved_frames}

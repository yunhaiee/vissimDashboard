"""GET /api/trajectories -- resolved per-timestep vehicle positions for the
Phase 3 trajectory-playback data layer (P4 builds the UI on top of this).

Reads a Vissim-style ``.fzp`` export via ``services/trajectory_reader.py``
and resolves every vehicle's (link, lane, pos) to absolute (x, y, angle)
against the ACTIVE dataset's network geometry -- same resolver primitives
Tier 1 already uses for static network elements. Never touches COM: this
endpoint only ever reads a static ``.fzp`` file from disk, whether the
synthetic demo export (``tools/make_demo_trajectories.py``) or, eventually, a
real Vissim-exported one dropped next to an uploaded dataset.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from config import DATA_DIR
from services.datasets import get_active_inpx_path
from services.inpx_parser import parse_network
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


@router.get("")
def get_trajectories(
    t0: float | None = Query(None, description="Trim window start (SIMSEC seconds, inclusive)"),
    t1: float | None = Query(None, description="Trim window end (SIMSEC seconds, inclusive)"),
    stride: int = Query(1, ge=1, description="Frame decimation -- keep 1 frame in every N"),
):
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
    if t0 is not None:
        meta["t0_filter"] = t0
    if t1 is not None:
        meta["t1_filter"] = t1

    return {"meta": meta, "frames": resolved_frames}

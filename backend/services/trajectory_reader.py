"""Parse Vissim's .fzp per-timestep vehicle-trajectory export and resolve it
against network geometry.

No real ``.fzp`` file exists on this dev machine (no COM, no lab box), so the
parser is written from Vissim's documented convention rather than a captured
sample: a handful of ``*``/``$`` header/comment lines, one ``$VEHICLE:...``
line listing the semicolon-separated column names present in every data row
(column order varies per export configuration), followed by one
semicolon-separated data row per vehicle per timestep. See
``docs/phase2/DECISIONS.md``'s dated entry for this unit -- same
inferred-defensive policy as the connector/element-topology entries.

``parse_fzp`` never raises on a malformed data row -- it is skipped and
counted in the returned ``meta``. ``resolve_frames`` turns each vehicle's
(link, lane, pos) into an absolute (x, y, angle) using the same
``point_at_pos``/``lane_offset_point`` primitives Tier 1 already resolves
static network elements with; a vehicle referencing an unknown link is
dropped from its frame (never given fabricated placement) and counted.
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from services.inpx_parser import lane_offset_point, point_at_pos

# The minimal column set every data row must resolve via the header before
# it can be parsed at all. LANE is intentionally excluded -- a real export
# can legitimately omit/blank it for a vehicle mid lane-change, and
# ``resolve_frames`` already falls back to ``point_at_pos`` when lane is
# unknown, so a missing/malformed LANE value degrades gracefully rather than
# discarding the whole row.
REQUIRED_COLUMNS = ("SIMSEC", "NO", "LINK", "POS", "SPEED")
OPTIONAL_COLUMNS = ("LANE",)


def parse_fzp(path: str | Path) -> dict:
    """Parse a ``.fzp`` file into ``{"meta": {...}, "frames": [...]}``.

    Cached by ``(path, mtime)`` (mirrors ``routers/geojson.py``'s
    ``_load_transform`` caching idiom) so repeated requests against an
    unchanged file are free; editing/regenerating the file invalidates the
    cache automatically since its mtime changes.
    """
    p = Path(path)
    mtime = os.path.getmtime(p)  # OSError/FileNotFoundError on a missing file -- caller's problem to 404 on
    return _parse_fzp_cached(str(p), mtime)


@lru_cache(maxsize=8)
def _parse_fzp_cached(path_str: str, mtime: float) -> dict:
    return _parse_fzp_impl(Path(path_str))


def _parse_fzp_impl(path: Path) -> dict:
    columns: list[str] | None = None
    col_index: dict[str, int] = {}
    frame_map: dict[float, dict[int, dict]] = {}
    skipped_rows = 0
    vehicle_ids: set[int] = set()

    text = path.read_text(encoding="utf-8", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("*"):
            continue
        if line.startswith("$"):
            if line.upper().startswith("$VEHICLE:"):
                header = line.split(":", 1)[1] if ":" in line else ""
                columns = [c.strip().upper() for c in header.split(";")]
                col_index = {name: i for i, name in enumerate(columns) if name}
            # Any other '$...' line (e.g. a $SIMRUN/$FILE-style header some
            # exports carry) is tolerated metadata, not a data row.
            continue

        # A data row requires a header that has already resolved every
        # required column; anything before/without that can't be interpreted
        # at all, so it's counted as skipped rather than guessed at.
        if columns is None or not all(name in col_index for name in REQUIRED_COLUMNS):
            skipped_rows += 1
            continue

        fields = line.split(";")
        try:
            simsec = float(fields[col_index["SIMSEC"]])
            veh_no = int(float(fields[col_index["NO"]].strip()))
            link = fields[col_index["LINK"]].strip()
            pos = float(fields[col_index["POS"]])
            speed = float(fields[col_index["SPEED"]])
        except (IndexError, ValueError):
            skipped_rows += 1
            continue

        if not link:
            skipped_rows += 1
            continue

        lane: int | None = None
        lane_idx = col_index.get("LANE")
        if lane_idx is not None and lane_idx < len(fields):
            lane_raw = fields[lane_idx].strip()
            if lane_raw:
                try:
                    lane = int(float(lane_raw))
                except ValueError:
                    lane = None

        t_key = round(simsec, 3)
        bucket = frame_map.setdefault(t_key, {})
        bucket[veh_no] = {"id": veh_no, "link": link, "lane": lane, "pos": pos, "speed": speed}
        vehicle_ids.add(veh_no)

    frames = [
        {"t": t, "vehicles": [frame_map[t][vid] for vid in sorted(frame_map[t])]}
        for t in sorted(frame_map)
    ]

    step = round(frames[1]["t"] - frames[0]["t"], 3) if len(frames) >= 2 else None

    meta = {
        "t0": frames[0]["t"] if frames else None,
        "t1": frames[-1]["t"] if frames else None,
        "step": step,
        "vehicle_count": len(vehicle_ids),
        "skipped_rows": skipped_rows,
    }
    return {"meta": meta, "frames": frames}


def resolve_frames(frames: list[dict], network_links: list[dict]) -> tuple[list[dict], int]:
    """Resolve each vehicle's (link, lane, pos) into absolute (x, y, angle).

    Uses ``lane_offset_point`` when a lane is known, falling back to
    ``point_at_pos`` (the link centerline) when it isn't -- matching
    ``inpx_parser``'s own defensive placement conventions. A vehicle
    referencing a link id absent from ``network_links`` (or one whose
    placement can't be resolved at all) is dropped from its frame rather than
    given fabricated coordinates; the running count of such drops is
    returned alongside the (otherwise same-shaped) frame list. Frames
    themselves are never dropped, even if every vehicle within one becomes
    unresolvable -- an empty ``vehicles`` list is still a valid frame.
    """
    link_index = {lk["id"]: lk for lk in network_links if lk.get("id")}
    skipped = 0
    resolved_frames: list[dict] = []

    for frame in frames:
        resolved_vehicles = []
        for veh in frame["vehicles"]:
            link = link_index.get(str(veh["link"]))
            if link is None:
                skipped += 1
                continue

            points = link.get("points")
            lane_widths = link.get("lane_widths")
            placement = None
            if veh.get("lane") is not None:
                placement = lane_offset_point(points, veh["pos"], veh["lane"], lane_widths)
            if placement is None:
                placement = point_at_pos(points, veh["pos"])
            if placement is None:
                skipped += 1
                continue

            x, y, angle = placement
            resolved_vehicles.append({**veh, "x": x, "y": y, "angle": angle})

        resolved_frames.append({"t": frame["t"], "vehicles": resolved_vehicles})

    return resolved_frames, skipped

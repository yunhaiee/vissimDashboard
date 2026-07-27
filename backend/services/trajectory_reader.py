"""Parse Vissim's .fzp per-timestep vehicle-trajectory export and resolve it
against network geometry.

Originally written from Vissim's documented convention (a handful of
``*``/``$`` header/comment lines, one ``$VEHICLE:...`` line listing the
semicolon-separated column names present in every data row, column order
varying per export configuration, followed by one semicolon-separated data
row per vehicle per timestep) since no real ``.fzp`` file existed on the
original dev machine. **Now verified live** against Vissim 2026 (Urban
Intersection Beijing.CN, ``backend/tools/vehicle_record_probe.py`` --
findings pasted verbatim in ``docs/phase2/DECISIONS.md``), which surfaced
real differences from that original assumption, folded in below via format
DETECTION rather than a second parsing pipeline:

  1. The default vehicle-record attribute selection carries no bare
     ``LINK``/``LANE`` columns at all -- Vissim exports
     ``LANE\\LINK\\NO``/``LANE\\INDEX`` (backslash-qualified sub-attribute
     paths) instead, and no COM-editable way to change that selection was
     found (``Evaluation.VehRecSelectedAttributes`` exists but every
     attempted mutation raised -- see ``vehicle_record_attrs_probe*.py``).
     ``_COLUMN_ALIASES`` below recognizes BOTH the real Vissim names and the
     original bare names (which the synthetic demo generator,
     ``tools/make_demo_trajectories.py``, and every existing test fixture
     already use) for the same canonical field.
  2. The default selection also carries no ``SPEED`` column at all -- moved
     from required to optional; a row/vehicle with no resolvable SPEED gets
     ``speed: None`` (never a fabricated ``0``, which would misrender a
     genuinely moving vehicle as stopped).
  3. A real export opens with a UTF-8 BOM immediately followed by a
     ``$VISION`` marker line. Reading with plain ``"utf-8"`` leaves the BOM
     glued to whatever text follows it, silently breaking that line's own
     ``*``/``$`` prefix check; reading with ``"utf-8-sig"`` strips it
     unconditionally regardless of position, which is what this module now
     does.

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

# Canonical field -> real Vissim header names that satisfy it, most-plausible
# first. Header-driven parsing already tries every column purely by name, so
# supporting both a real Vissim export's shape AND this codebase's original
# synthetic/test shape is just a matter of trying multiple candidate names
# per canonical field -- see the module docstring's live-probe findings.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "SIMSEC": ("SIMSEC",),
    "NO": ("NO",),
    "LINK": ("LINK", "LANE\\LINK\\NO"),
    "LANE": ("LANE", "LANE\\INDEX"),
    "POS": ("POS",),
    "SPEED": ("SPEED",),
}

# The minimal column set every data row must resolve via the header before it
# can be parsed at all. LANE and SPEED are intentionally excluded -- a real
# export's default attribute selection carries neither (see module
# docstring), and ``resolve_frames``/callers already degrade gracefully
# (falling back to ``point_at_pos`` when lane is unknown; leaving
# ``speed: None`` when absent) rather than needing either value to render a
# frame at all.
REQUIRED_COLUMNS = ("SIMSEC", "NO", "LINK", "POS")
OPTIONAL_COLUMNS = ("LANE", "SPEED")


def _resolve_columns(columns: list[str]) -> dict[str, int]:
    """Map each canonical field present (via any of its aliases) in
    ``columns`` to that column's index. A field with no matching alias in
    this particular header is simply absent from the returned dict --
    callers use ``.get(...)`` for optional fields and check membership for
    required ones."""
    col_index = {name: i for i, name in enumerate(columns) if name}
    resolved: dict[str, int] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in col_index:
                resolved[canonical] = col_index[alias]
                break
    return resolved


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
    resolved: dict[str, int] | None = None
    frame_map: dict[float, dict[int, dict]] = {}
    skipped_rows = 0
    vehicle_ids: set[int] = set()

    # utf-8-sig strips a leading UTF-8 BOM unconditionally regardless of
    # where python thinks "the start of the file" is -- a real Vissim export
    # opens with one immediately followed by a "$VISION" marker line (see
    # module docstring point 3); plain "utf-8" would leave it glued to that
    # line's first character, breaking its "*"/"$" prefix check.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
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
                resolved = _resolve_columns(columns)
            # Any other '$...' line (e.g. a $VISION/$SIMRUN/$FILE-style
            # header some exports carry) is tolerated metadata, not a data
            # row.
            continue

        # A data row requires a header that has already resolved every
        # required column; anything before/without that can't be interpreted
        # at all, so it's counted as skipped rather than guessed at.
        if resolved is None or not all(name in resolved for name in REQUIRED_COLUMNS):
            skipped_rows += 1
            continue

        fields = line.split(";")
        try:
            simsec = float(fields[resolved["SIMSEC"]])
            veh_no = int(float(fields[resolved["NO"]].strip()))
            link = fields[resolved["LINK"]].strip()
            pos = float(fields[resolved["POS"]])
        except (IndexError, ValueError):
            skipped_rows += 1
            continue

        if not link:
            skipped_rows += 1
            continue

        lane: int | None = None
        lane_idx = resolved.get("LANE")
        if lane_idx is not None and lane_idx < len(fields):
            lane_raw = fields[lane_idx].strip()
            if lane_raw:
                try:
                    lane = int(float(lane_raw))
                except ValueError:
                    lane = None

        # SPEED is optional (see module docstring point 2) -- a real
        # export's default attribute selection has no SPEED column at all.
        # Absent/blank/malformed all degrade to None, never a fabricated 0
        # (which would misrender a genuinely moving vehicle as stopped).
        speed: float | None = None
        speed_idx = resolved.get("SPEED")
        if speed_idx is not None and speed_idx < len(fields):
            speed_raw = fields[speed_idx].strip()
            if speed_raw:
                try:
                    speed = float(speed_raw)
                except ValueError:
                    speed = None

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

"""Load real-world (observed) traffic counts from the reference Excel file."""
from __future__ import annotations

import datetime
from collections.abc import Iterable
from io import BytesIO
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

try:
    import openpyxl
    from openpyxl.styles import Font
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

# Decompression-bomb guard, called BEFORE every openpyxl.load_workbook() below
# touches its input -- see services/dataimport/xlsx_guard.py's module
# docstring for the measured numbers behind this. Depends on stdlib zipfile
# only, so it is imported unconditionally (independent of _HAS_OPENPYXL).
from services.dataimport.xlsx_guard import guard_xlsx_bytes, guard_xlsx_path


class DirectionCount(TypedDict):
    direction: str   # e.g. "N-E"
    real: int
    sim_basam: int   # BaseAM simulation value from Excel


class NodeRealCounts(TypedDict):
    node_id: str
    node_name: str
    total_real: int | None
    total_sim_basam: int | None
    directions: list[DirectionCount]


class BaseWorkbookCountRow(TypedDict):
    period: str
    node_id: str
    node_name: str
    movement: str
    observed_volume: float | None
    model_volume: float | None


class BaseWorkbookTravelTimeRow(TypedDict):
    period: str
    route_id: str
    route_name: str
    sample_order: int | None
    time_label: str
    observed_travel_time_sec: float | None
    model_travel_time_sec: float | None
    # Route distance (DisTrav(All), metres) — drives the chart's distance axis.
    dist_m: float | None
    # Optional explicit validation-chart values (from the dedicated template
    # columns: "TomTom avg" / "model output" / "TomTom Avg -15%" / "+15%").
    # When present these override the chart's observed/model/bands.
    tomtom_avg_sec: float | None
    model_output_sec: float | None
    tomtom_low_sec: float | None
    tomtom_high_sec: float | None


STANDARD_COUNTS_HEADERS = [
    "period",
    "node_id",
    "node_name",
    "movement",
    "observed_volume",
    "model_volume",
]
STANDARD_TRAVEL_TIME_HEADERS = [
    "period",
    "route_id",
    "route_name",
    "sample_order",
    "time_label",
    "observed_travel_time_sec",
    "model_travel_time_sec",
]

COUNT_HEADER_ALIASES = {
    "period": {"period", "시간대구분", "구분"},
    "time_range": {"time_range", "time", "시간", "시간대", "interval", "timeint"},
    "node_id": {"node_id", "node", "node no", "교차로id", "교차로_id"},
    "node_name": {"node_name", "node", "intersection", "intersection_name", "교차로", "교차로명", "지점"},
    "movement": {"movement", "이동류"},
    "movement_direction": {"movement\\direction", "movement_direction", "direction", "turn", "방향"},
    "observed_volume": {"observed_volume", "observed", "actual", "real", "실제", "실측", "관측"},
    "model_volume": {"model_volume", "model", "simulation", "sim", "vehs(all)", "vehs_all", "vehs", "시뮬", "시뮬레이션", "모델"},
    "stat_type": {"stat_type", "type", "average", "집계", "simrun"},
    "queue_length": {"qlen", "queue_length"},
    "queue_length_max": {"qlenmax", "queue_length_max"},
    "persons": {"pers(all)", "pers_all", "persons"},
}


def _parse_node_id(col3: str) -> str:
    """Extract numeric node id from '1: 도서관사거리 - ...' strings."""
    m = re.match(r"^\s*(\d+)\s*:", col3)
    return m.group(1) if m else ""


def _parse_node_name(col3: str) -> str:
    """Extract node name from '1: 도서관사거리 - ...' strings."""
    m = re.match(r"^\s*\d+\s*:\s*([^-]+)", col3)
    return m.group(1).strip() if m else col3.strip()


def _leading_node_id(value: str) -> str:
    """Extract a leading integer node id.

    Handles the Vissim Node Results movement format where the node number
    prefixes the link description, e.g. '1 - 513: South St EB@26.2 - 515: ...'
    or a bare node number '1' (the Total row), neither of which has the '1:'
    shape that :func:`_parse_node_id` expects.
    """
    m = re.match(r"^\s*(\d+)", value)
    return m.group(1) if m else ""


@lru_cache(maxsize=8)
def load_real_counts(excel_path: str, period: str | None = None) -> dict[str, NodeRealCounts]:
    """Parse the reference Excel and return {node_id: NodeRealCounts}.

    Prefers the current ``Counts`` sheet layout, parsing it through the same
    :func:`_parse_counts_sheet` used by the validation dashboard so it
    understands the SouthSt "1 - 513: link" movement format. Falls back to the
    legacy ``BaseAM`` sheet ("1: name" ids) for older workbooks. Caches the
    result so the file is only read once per process lifetime.
    """
    if not _HAS_OPENPYXL:
        return {}

    p = Path(excel_path)
    if not p.exists():
        return {}

    guard_xlsx_path(p)
    wb = openpyxl.load_workbook(p, data_only=True)

    if "Counts" in wb.sheetnames:
        return _real_counts_from_count_rows(_parse_counts_sheet(wb["Counts"], []), period)
    if "BaseAM" in wb.sheetnames:
        # The legacy BaseAM sheet only holds AM counts.
        if period and period.upper() != "AM":
            return {}
        return _real_counts_from_baseam_sheet(wb["BaseAM"])
    return {}


def _as_int(value: float | None) -> int | None:
    return None if value is None else int(round(value))


def _real_counts_from_count_rows(
    rows: list[BaseWorkbookCountRow], period: str | None = None
) -> dict[str, NodeRealCounts]:
    """Aggregate normalized Counts rows into per-node observed totals.

    Each node carries an explicit ``Total`` movement row (the node total);
    directional rows ("W-E", "N-SE", ...) are kept for the breakdown. When a
    node has no ``Total`` row its directional volumes are summed instead. Rows
    are restricted to a single period so AM and PM counts are never mixed into
    one total. When ``period`` is given ("AM"/"PM") that period is selected
    (returning ``{}`` if it is absent); otherwise it auto-selects AM when
    present, else the first period found (backward-compatible default).
    """
    if not rows:
        return {}

    if period:
        target = period.upper()
        if not any(row["period"] == target for row in rows):
            return {}
    else:
        target = "AM" if any(row["period"] == "AM" for row in rows) else rows[0]["period"]

    result: dict[str, NodeRealCounts] = {}
    for row in rows:
        if row["period"] != target:
            continue
        node_id = row["node_id"]
        node = result.get(node_id)
        if node is None:
            node = NodeRealCounts(
                node_id=node_id,
                node_name=row["node_name"],
                total_real=None,
                total_sim_basam=None,
                directions=[],
            )
            result[node_id] = node

        if row["movement"] == "Total":
            node["total_real"] = _as_int(row["observed_volume"])
            node["total_sim_basam"] = _as_int(row["model_volume"])
        else:
            node["directions"].append(
                DirectionCount(
                    direction=row["movement"],
                    real=_as_int(row["observed_volume"]) or 0,
                    sim_basam=_as_int(row["model_volume"]) or 0,
                )
            )

    # Fall back to summing directional movements for nodes with no Total row.
    for node in result.values():
        if node["total_real"] is None and node["directions"]:
            node["total_real"] = sum(direction["real"] for direction in node["directions"])
            node["total_sim_basam"] = sum(direction["sim_basam"] for direction in node["directions"])

    return result


def _real_counts_from_baseam_sheet(ws) -> dict[str, NodeRealCounts]:
    """Legacy parser for the original ``BaseAM`` sheet layout ("1: name" ids)."""
    result: dict[str, NodeRealCounts] = {}
    for row in ws.iter_rows(values_only=True):
        col3 = row[2]
        direction = row[3]
        sim_val = row[4]
        real_val = row[5]

        if col3 is None or direction is None:
            continue

        col3 = str(col3)
        direction = str(direction)

        node_id = _parse_node_id(col3)
        if not node_id:
            continue

        if node_id not in result:
            result[node_id] = NodeRealCounts(
                node_id=node_id,
                node_name=_parse_node_name(col3),
                total_real=None,
                total_sim_basam=None,
                directions=[],
            )

        if direction == "Total":
            result[node_id]["total_real"] = int(real_val) if real_val is not None else None
            result[node_id]["total_sim_basam"] = int(sim_val) if sim_val is not None else None
        elif "-" in direction:
            result[node_id]["directions"].append(
                DirectionCount(
                    direction=direction,
                    real=int(real_val) if real_val is not None else 0,
                    sim_basam=int(sim_val) if sim_val is not None else 0,
                )
            )

    return result


def _base_workbook_payload_from_workbook(wb, filename: str, path: str) -> dict[str, Any]:
    """Build the normalized base-workbook payload from an already-open
    ``openpyxl`` workbook. Shared by :func:`load_base_workbook` (reads from
    the active workbook's path) and :func:`load_base_workbook_bytes` (parses
    freshly-uploaded bytes in memory, before any decision to persist them) so
    the two never drift on what counts as a "clean" parse.
    """
    payload: dict[str, Any] = {
        "exists": True,
        "filename": filename,
        "path": path,
        "counts": [],
        "travel_times": [],
        "warnings": [],
    }

    payload["sheets"] = list(wb.sheetnames)
    if "Counts" in wb.sheetnames:
        payload["counts"] = _parse_counts_sheet(wb["Counts"], payload["warnings"])
    else:
        payload["warnings"].append("Counts sheet was not found.")

    # Distinct periods actually present in the Counts sheet, AM before PM, so the
    # UI can show only the time periods the uploaded workbook contains.
    present = {row["period"] for row in payload["counts"] if row.get("period")}
    payload["periods"] = [p for p in ("AM", "PM") if p in present] + sorted(present - {"AM", "PM"})

    if "TravelTimes" in wb.sheetnames:
        payload["travel_times"] = _parse_travel_times_sheet(wb["TravelTimes"], payload["warnings"])
    else:
        payload["warnings"].append("TravelTimes sheet was not found.")

    return payload


@lru_cache(maxsize=8)
def load_base_workbook(excel_path: str) -> dict[str, Any]:
    """Parse the standard base-model validation workbook (read from disk)."""
    p = Path(excel_path)
    if not _HAS_OPENPYXL:
        return {
            "exists": p.exists(),
            "filename": p.name,
            "path": str(p),
            "counts": [],
            "travel_times": [],
            "warnings": ["openpyxl is not installed."],
        }
    if not p.exists():
        return {
            "exists": False,
            "filename": p.name,
            "path": str(p),
            "counts": [],
            "travel_times": [],
            "warnings": ["Validation workbook was not found."],
        }

    try:
        guard_xlsx_path(p)
        wb = openpyxl.load_workbook(p, data_only=True)
    except Exception as exc:
        return {
            "exists": True,
            "filename": p.name,
            "path": str(p),
            "counts": [],
            "travel_times": [],
            "warnings": [f"Failed to open validation workbook: {exc}"],
        }

    return _base_workbook_payload_from_workbook(wb, p.name, str(p))


def load_base_workbook_bytes(filename: str, content: bytes) -> dict[str, Any]:
    """Parse freshly-uploaded workbook bytes WITHOUT touching disk or the
    active workbook.

    Used by ``POST /api/validation/base-workbook`` (routers/validation.py) to
    decide, BEFORE persisting anything, whether an upload conforms to the
    standard template -- the stage-then-promote flow this pairs with, so a
    non-conforming upload never displaces the active workbook. Returns the
    exact same shape :func:`load_base_workbook` does (parsed via the same
    ``_base_workbook_payload_from_workbook``), just with ``path`` empty since
    there is no persisted file yet. See docs/phase2/DECISIONS.md if an entry
    was recorded for this.
    """
    name = Path(filename).name or "real_counts.xlsx"
    if not _HAS_OPENPYXL:
        return {
            "exists": False,
            "filename": name,
            "path": "",
            "counts": [],
            "travel_times": [],
            "warnings": ["openpyxl is not installed."],
        }

    try:
        guard_xlsx_bytes(content, label=name)
        wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        return {
            "exists": False,
            "filename": name,
            "path": "",
            "counts": [],
            "travel_times": [],
            "warnings": [f"Uploaded file could not be read as an Excel workbook: {exc}"],
        }

    return _base_workbook_payload_from_workbook(wb, name, "")


def create_base_workbook_template() -> bytes:
    """Build a standard workbook template as bytes."""
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is not installed.")

    wb = openpyxl.Workbook()
    counts = wb.active
    counts.title = "Counts"
    # The "Period" column makes AM/PM explicit. Fill it with "AM" or "PM" for
    # every row. (It is also inferred from a TimeInt like 08:xx/09:xx -> AM or
    # 17:xx/18:xx -> PM, but a raw second-based VISSIM TimeInt such as
    # "61200-64800" cannot be classified, so always set Period for PM rows.)
    # Columns mirror the VISSIM Node Results export: Movement (node id + name) and
    # a separate Direction column (the turn movement: N-E, W-S, Total, ...).
    # Vehs(All) is the simulated/model volume; Observed is the real-world count.
    counts.append(["SimRun", "TimeInt", "Period", "Movement", "Direction", "QLen", "QLenMax", "Vehs(All)", "Observed"])
    counts.append(["Average", "900-4500", "AM", "1: 도서관사거리", "N-N", 0.03, 14.72, 4, 4])
    counts.append(["Average", "900-4500", "AM", "1: 도서관사거리", "N-E", 5.91, 27.03, 72, 72])
    counts.append(["Average", "900-4500", "AM", "1: 도서관사거리", "N-S", 5.91, 27.03, 63, 68])
    counts.append(["Average", "900-4500", "AM", "1: 도서관사거리", "Total", 4.49, 86.73, 1722, 1731])
    counts.append(["Average", "900-4500", "AM", "2: 주차장 입구", "W-S", 3.12, 41.22, 34, 35])
    counts.append(["Average", "900-4500", "AM", "2: 주차장 입구", "W-E", 3.12, 41.22, 620, 612])
    counts.append(["Average", "900-4500", "AM", "2: 주차장 입구", "Total", 1.08, 41.22, 1209, 1215])
    counts.append([])
    # PM example rows — replace the Observed values with the PM peak counts.
    counts.append(["Average", "61200-64800", "PM", "1: 도서관사거리", "N-N", 0.04, 16.10, 6, 6])
    counts.append(["Average", "61200-64800", "PM", "1: 도서관사거리", "N-E", 6.20, 29.40, 88, 90])
    counts.append(["Average", "61200-64800", "PM", "1: 도서관사거리", "N-S", 6.20, 29.40, 70, 74])
    counts.append(["Average", "61200-64800", "PM", "1: 도서관사거리", "Total", 4.80, 90.10, 1880, 1905])
    counts.append(["Average", "61200-64800", "PM", "2: 주차장 입구", "W-S", 3.40, 44.10, 41, 43])
    counts.append(["Average", "61200-64800", "PM", "2: 주차장 입구", "W-E", 3.40, 44.10, 690, 688])
    counts.append(["Average", "61200-64800", "PM", "2: 주차장 입구", "Total", 1.20, 44.10, 1330, 1352])

    # TravelTimes mirrors the Vissim "Vehicle Travel Time Measurement" export so
    # the analyst can paste it in as-is. TravelTime is the simulated (model)
    # value; the Observed column is left blank for the real-world travel time.
    travel_times = wb.create_sheet("TravelTimes")
    # The last four columns hold the travel-time validation values (as mm:ss):
    #   TomTom avg        = observed average travel time
    #   TomTom Avg -15%/+15% = the validation band around the observed average
    #   model output      = the modelled travel time
    # These feed the cumulative travel-time chart (observed +/-15% vs model).
    travel_header = [
        "SimRun", "TimeInt", "VehicleTravelTimeMeasurement",
        "Vehs(All)", "TravelTime", "DisTrav(All)", "Observed",
        "TomTom Avg -15%", "model output", "TomTom avg", "TomTom Avg 15%",
    ]
    _t = datetime.time
    travel_sample = {
        "AM": [
            ["Average", "900-4500", "1: RoeHwy,MurdochDr_NB", 342, 225.062166, 2137.159562, 336.98,
             _t(0, 4, 46, 450000), _t(0, 5, 12, 46000), _t(0, 5, 37), _t(0, 6, 27, 550000)],
            ["Average", "900-4500", "2: MurdochDr,RoeHwy_SB", 63, 263.484780, 2067.991296, 236.77,
             _t(0, 3, 21, 450000), _t(0, 4, 17, 935000), _t(0, 3, 57), _t(0, 4, 32, 550000)],
        ],
        "PM": [
            ["Average", "900-4500", "1: RoeHwy,MurdochDr_NB", 118, 208.231189, 2137.159562, 241.34,
             _t(0, 3, 24, 850000), _t(0, 3, 26, 533000), _t(0, 4, 1), _t(0, 4, 37, 150000)],
            ["Average", "900-4500", "2: MurdochDr,RoeHwy_SB", 86, 236.268230, 2067.991296, 260.68,
             _t(0, 3, 41, 850000), _t(0, 3, 57, 223000), _t(0, 4, 21), _t(0, 5, 0, 150000)],
        ],
    }
    travel_header_rows: list[int] = []
    for period in ("AM", "PM"):
        travel_times.append([period])
        travel_times.append(travel_header)
        travel_header_rows.append(travel_times.max_row)
        for sample_row in travel_sample[period]:
            travel_times.append(sample_row)
        travel_times.append([])

    bold = Font(bold=True)

    counts.freeze_panes = "A2"
    for cell in counts[1]:
        cell.font = bold
    for column_cells in counts.columns:
        max_len = max(len(str(cell.value or "")) for cell in column_cells)
        counts.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 32)

    for row_index in travel_header_rows:
        for cell in travel_times[row_index]:
            cell.font = bold
    for row in travel_times.iter_rows():
        if row and _clean_string(row[0].value) in {"AM", "PM"}:
            row[0].font = bold
    for column_cells in travel_times.columns:
        max_len = max(len(str(cell.value or "")) for cell in column_cells)
        travel_times.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 36)
    # Render the four validation-time columns (H-K) as mm:ss.
    for row in travel_times.iter_rows():
        for cell in row:
            if isinstance(cell.value, datetime.time):
                cell.number_format = "mm:ss"

    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def clear_validation_caches() -> None:
    load_real_counts.cache_clear()
    load_base_workbook.cache_clear()


# GEH bands (MRWA/DMRB): <5 good, 5-10 fair, >=10 poor. One spelling, shared.
GEH_GOOD_LT: float = 5.0
GEH_FAIR_LT: float = 10.0


def geh(model: float | None, observed: float | None) -> float | None:
    """GEH statistic – standard traffic model validation metric.

    THE single backend implementation (unit 1.5 D1/D2). Every backend caller
    imports this one; ``report_exports``/``report_builder`` no longer carry
    their own copy.

    Returns ``None`` – never a number – when the statistic is undefined:
      * either input missing, or
      * either input NON-FINITE (NaN / ±Infinity), or
      * ``model + observed <= 0`` (a 0-observed/0-modelled row carries no
        information, and the old ``report_exports`` copy's ``0.0`` there was a
        silent *passing* grade on a customer-facing report), or
      * the result itself overflows to non-finite.

    Deliberately unrounded: each render site rounds to its own display
    precision, so no caller inherits another's.
    """
    if model is None or observed is None:
        return None
    # NON-FINITE GUARD — must match frontend/lib/geh.ts exactly.
    # A NaN or ±Infinity input means an upstream divide-by-zero or a bad parse,
    # not a measurement, so the statistic is UNDEFINED. Without this guard NaN
    # flowed straight through: ``geh_grade(nan)`` failed both ``<`` comparisons
    # and fell out as ``"poor"`` — a confident bad grade invented from nothing —
    # and ``geh_pass_rate`` counted NaN in the DENOMINATOR while reporting
    # ``excluded: 0``. "Undefined" beats "poor".
    if not math.isfinite(model) or not math.isfinite(observed):
        return None
    denom = model + observed
    if denom <= 0:
        return None
    try:
        value = math.sqrt(2.0 * (model - observed) ** 2 / denom)
    except OverflowError:
        # Python raises here where JS silently yields Infinity (e.g. model=1e200,
        # observed=1). Both runtimes must answer None, not two different wrongs.
        return None
    # ``2.0 * x`` does NOT raise on overflow — it returns inf — so the result
    # needs its own guard on top of the try/except.
    return value if math.isfinite(value) else None


def geh_grade(value: float | None) -> str | None:
    """``good`` / ``fair`` / ``poor`` for a GEH value; ``None`` when undefined.

    Bands are the ones already used everywhere (<5 / <10 / else); this just
    stops each consumer re-spelling them. Non-finite values are undefined, NOT
    ``poor``: NaN fails both ``<`` comparisons, so without the guard it fell
    through to the final ``return "poor"``.
    """
    if value is None or not math.isfinite(value):
        return None
    if value < GEH_GOOD_LT:
        return "good"
    if value < GEH_FAIR_LT:
        return "fair"
    return "poor"


def geh_pass_rate(values: Iterable[float | None], threshold: float = GEH_GOOD_LT) -> dict:
    """Aggregate GEH values into ``{passed, total, excluded, rate}``.

    ``None`` **and non-finite** values (undefined GEH – see :func:`geh`) are
    excluded from BOTH the numerator and the denominator, and counted in
    ``excluded`` so a caller can disclose the shrunken denominator instead of
    hiding it. Matches ``gehPassRate`` in frontend/lib/geh.ts: without the
    non-finite half, ``-inf < threshold`` scored a free PASS and ``nan``
    inflated the denominator while ``excluded`` still read 0.
    """
    materialized = list(values)
    usable = [value for value in materialized if value is not None and math.isfinite(value)]
    passed = sum(1 for value in usable if value < threshold)
    return {
        "passed": passed,
        "total": len(usable),
        "excluded": len(materialized) - len(usable),
        "rate": (passed / len(usable) * 100.0) if usable else 0.0,
    }


def pct_error(sim: float | None, obs: float | None) -> float | None:
    """Percent error: (sim - obs) / obs * 100. None when obs is 0 or missing."""
    if obs is None or obs == 0 or sim is None:
        return None
    return (sim - obs) / obs * 100.0


def _column_is_direction_like(ws, col_idx: int) -> bool:
    """True if a column mostly holds movement-direction tokens (W-E, N-S, Total)."""
    pattern = re.compile(r"^-?[NSEW]{1,3}-[NSEW]{1,3}$", re.IGNORECASE)
    total = 0
    hits = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if col_idx >= len(row):
            continue
        value = _clean_string(row[col_idx])
        if not value:
            continue
        total += 1
        if pattern.match(value) or value.lower() == "total":
            hits += 1
        if total >= 25:
            break
    return total > 0 and hits / total >= 0.6


def _adjust_for_unlabeled_direction_column(ws, headers: dict[str, int]) -> None:
    """Handle the raw Vissim Node Results export where the Movement column is
    followed by an *unlabeled* turn-direction column. The pasted header then
    omits that column, so every later column is shifted one to the left and
    model_volume ends up pointing at a QLen-type column instead of Vehs(All).
    Detect the direction column by content and re-point movement_direction and
    model_volume (Vissim columns end in [..., Vehs(All), Observed]).
    """
    movement_idx = headers.get("movement")
    if movement_idx is None or "movement_direction" in headers:
        return
    candidate = movement_idx + 1
    if not _column_is_direction_like(ws, candidate):
        return
    headers["movement_direction"] = candidate
    observed_idx = headers.get("observed_volume")
    if observed_idx is not None and observed_idx - 1 > candidate:
        headers["model_volume"] = observed_idx - 1


def _parse_counts_sheet(
    ws,
    warnings: list[str],
    headers: dict[str, int] | None = None,
    min_row: int = 2,
) -> list[BaseWorkbookCountRow]:
    """Build normalized count rows from a worksheet.

    ``headers`` defaults to ``None``, which preserves the original behavior
    exactly: canonical headers are auto-detected from row 1 via
    :func:`_canonical_headers_for_sheet` + :func:`COUNT_HEADER_ALIASES`, the
    sheet is rejected (with a warning) unless it matches one of the three
    known shapes, and data starts at row 2.

    The mapping-import feature (``services/workbook_mapping.py``) instead
    passes an EXPLICIT ``headers`` dict (canonical field name -> 0-indexed
    column, chosen by the user via the mapping UI) and a ``min_row`` for the
    row right after whatever header row the user picked. That reuses this
    exact same row-building logic (node id/name parsing, movement
    normalization, period detection, ...) so downstream GEH/validation math
    never diverges between the two import paths — the explicit-mapping
    caller is trusted to have already checked required fields itself (see
    ``validate_counts_mapping``), so the shape-detection gate below is
    skipped in that case.
    """
    if headers is None:
        headers = _canonical_headers_for_sheet(ws, COUNT_HEADER_ALIASES)
        _adjust_for_unlabeled_direction_column(ws, headers)
        has_standard_shape = {"period", "node_id", "node_name", "movement", "observed_volume"}.issubset(headers)
        has_simple_shape = {"time_range", "node_name", "movement", "observed_volume"}.issubset(headers)
        has_vissim_shape = {"time_range", "movement", "model_volume", "observed_volume"}.issubset(headers)
        if not has_standard_shape and not has_simple_shape and not has_vissim_shape:
            warnings.append(
                "Counts sheet is missing required columns. Use either "
                "period/node_id/node_name/movement/observed_volume or "
                "time_range/node_name/movement/observed_volume or "
                "Vissim Node Results columns TimeInt/Movement/Vehs(All)/Observed."
            )
            return []
    else:
        # An explicit mapping never carries the packed Vissim "1: name W-E"
        # movement string -- the user maps a plain movement column directly.
        has_vissim_shape = False

    rows: list[BaseWorkbookCountRow] = []
    for excel_row in ws.iter_rows(min_row=min_row, values_only=True):
        values = _row_as_dict(headers, excel_row)
        period = _normalize_period(values.get("period")) or _period_from_time_range(values.get("time_range"))
        raw_node_id = _clean_string(values.get("node_id"))
        raw_node_name = _clean_string(values.get("node_name"))
        raw_movement = _clean_string(values.get("movement"))
        raw_movement_direction = _clean_string(values.get("movement_direction"))
        parsed_vissim_movement = _parse_vissim_movement(raw_movement) if has_vissim_shape else None
        node_id = (
            raw_node_id
            or _parse_node_id(raw_node_name)
            or _parse_node_id(raw_movement)
            or (parsed_vissim_movement[0] if parsed_vissim_movement else "")
            or _leading_node_id(raw_movement)
        )
        node_name = (
            _parse_node_name(raw_node_name)
            or (parsed_vissim_movement[1] if parsed_vissim_movement else "")
            # Only treat the movement string as a node name when it has the
            # '1: name' shape; the '1 - 513: link - ...' form has no clean name.
            or (_parse_node_name(raw_movement) if _parse_node_id(raw_movement) else "")
            or f"Node {node_id}"
        )
        movement_source = raw_movement_direction or (parsed_vissim_movement[2] if parsed_vissim_movement else raw_movement)
        movement = _normalize_movement(movement_source)
        if not period or not node_id or not movement:
            continue

        rows.append(
            {
                "period": period,
                "node_id": node_id,
                "node_name": node_name,
                "movement": movement,
                "observed_volume": _coerce_float(values.get("observed_volume")),
                "model_volume": _coerce_float(values.get("model_volume")),
            }
        )
    return rows


TRAVEL_TIME_VISSIM_ALIASES = {
    "stat_type": {"stat_type", "simrun", "type", "average", "집계"},
    "time_range": {"time_range", "timeint", "time", "interval", "시간", "시간대"},
    "measurement": {
        "measurement",
        "route",
        "vehicletraveltimemeasurement",
        "vehicletravletimemeasurement",  # tolerate the common "Travle" typo
        "traveltimemeasurement",
        "vehicle_travel_time_measurement",
        "통행시간측정",
    },
    "vehs": {"vehs(all)", "vehs_all", "vehs", "교통량"},
    # Vissim's simulated travel time -> the model value.
    "model_travel_time": {
        "traveltime",
        "travel_time",
        "travtm",
        "travtm(all)",
        "model_travel_time_sec",
        "통행시간",
    },
    "dist": {"distrav(all)", "distrav", "disttrav(all)", "disttrav", "disttrav_all", "dist"},
    # Dedicated travel-time validation-chart columns (mm:ss times). When present
    # they override the chart's observed / model / band values.
    "tomtom_avg": {"tomtom avg", "tomtom_avg", "tomtom average"},
    "model_output": {"model output", "model_output"},
    "tomtom_low": {"tomtom avg -15%", "tomtom_avg_-15%", "tomtom -15%"},
    "tomtom_high": {"tomtom avg 15%", "tomtom_avg_15%", "tomtom avg +15%", "tomtom 15%"},
    # Real-world / field travel time entered by the analyst -> the observed value.
    "observed": {
        "observed",
        "actual",
        "real",
        "observed_travel_time_sec",
        "실측",
        "관측",
        "실제",
        "실제통행시간",
    },
}


def _parse_travel_times_sheet(ws, warnings: list[str]) -> list[BaseWorkbookTravelTimeRow]:
    """Parse the TravelTimes sheet into the normalized row contract.

    Two layouts are accepted:

    * the canonical layout — ``period``/``route_id``/``route_name``/
      ``sample_order``/``time_label``/``observed_travel_time_sec``/
      ``model_travel_time_sec`` (header in row 1); and
    * a Vissim "Vehicle Travel Time Measurement" export — stacked ``AM``/``PM``
      section headers, each followed by columns ``SimRun``/``TimeInt``/
      ``VehicleTravelTimeMeasurement``/``Vehs(All)``/``TravelTime``/
      ``DisTrav(All)`` plus an optional ``Observed`` column. There ``TravelTime``
      is the simulated (model) value and ``Observed`` is the real-world travel
      time. The period comes from the section header because the simulation
      ``TimeInt`` (e.g. ``900-4500``) is identical for AM and PM.
    """
    headers = _headers_for_sheet(ws)
    if not [header for header in STANDARD_TRAVEL_TIME_HEADERS if header not in headers]:
        return _parse_travel_times_standard(ws)

    rows = _parse_travel_times_vissim(ws)
    if rows:
        return rows

    warnings.append(
        "TravelTimes sheet is missing required columns. Use either "
        "period/route_id/route_name/sample_order/time_label/observed_travel_time_sec/model_travel_time_sec, "
        "or a Vissim travel time export with AM/PM section headers and columns "
        "TimeInt/VehicleTravelTimeMeasurement/TravelTime (+ optional Observed)."
    )
    return []


def _parse_travel_times_standard(
    ws,
    headers: dict[str, int] | None = None,
    min_row: int = 2,
) -> list[BaseWorkbookTravelTimeRow]:
    """Build normalized travel-time rows from a worksheet using the canonical
    (non-Vissim-export) header shape.

    Same ``headers``/``min_row`` override contract as :func:`_parse_counts_sheet`
    -- ``None`` preserves the exact original auto-detected-row-1 behavior;
    the mapping-import feature passes an explicit column mapping + the row
    right after the user-picked header row, reusing this exact row-building
    logic.
    """
    if headers is None:
        headers = _headers_for_sheet(ws)
    rows: list[BaseWorkbookTravelTimeRow] = []
    for excel_row in ws.iter_rows(min_row=min_row, values_only=True):
        values = _row_as_dict(headers, excel_row)
        period = _normalize_period(values.get("period"))
        route_id = _clean_string(values.get("route_id"))
        if not period or not route_id:
            continue

        rows.append(
            {
                "period": period,
                "route_id": route_id,
                "route_name": _clean_string(values.get("route_name")) or f"Route {route_id}",
                "sample_order": _coerce_int(values.get("sample_order")),
                "time_label": _clean_string(values.get("time_label")),
                "observed_travel_time_sec": _coerce_float(values.get("observed_travel_time_sec")),
                "model_travel_time_sec": _coerce_float(values.get("model_travel_time_sec")),
                "dist_m": _coerce_float(values.get("dist") or values.get("dist_m")),
                "tomtom_avg_sec": _time_to_sec(values.get("tomtom_avg")),
                "model_output_sec": _time_to_sec(values.get("model_output")),
                "tomtom_low_sec": _time_to_sec(values.get("tomtom_low")),
                "tomtom_high_sec": _time_to_sec(values.get("tomtom_high")),
            }
        )
    return rows


def _parse_travel_times_vissim(ws) -> list[BaseWorkbookTravelTimeRow]:
    rows: list[BaseWorkbookTravelTimeRow] = []
    current_period = ""
    columns: dict[str, int] = {}

    for excel_row in ws.iter_rows(values_only=True):
        cells = list(excel_row)
        first = _clean_string(cells[0]) if cells else ""
        rest_empty = all(not _clean_string(cell) for cell in cells[1:])

        # A lone "AM"/"PM" cell is a section header that sets the active period.
        marker = _normalize_period(first)
        if marker and rest_empty:
            current_period = marker
            continue

        # Detect the Vissim column header row anywhere in the sheet.
        candidate = _canonical_row_headers(cells, TRAVEL_TIME_VISSIM_ALIASES)
        if "measurement" in candidate and ("model_travel_time" in candidate or "vehs" in candidate):
            columns = candidate
            continue

        if not columns or not current_period:
            continue

        measurement = _clean_string(_cell_at(cells, columns.get("measurement")))
        route_id = _parse_route_id(measurement)
        if not route_id:
            continue

        rows.append(
            {
                "period": current_period,
                "route_id": route_id,
                "route_name": _parse_route_name(measurement) or f"Route {route_id}",
                "sample_order": None,
                "time_label": _clean_string(_cell_at(cells, columns.get("time_range"))),
                "observed_travel_time_sec": _coerce_float(_cell_at(cells, columns.get("observed"))),
                "model_travel_time_sec": _coerce_float(_cell_at(cells, columns.get("model_travel_time"))),
                "dist_m": _coerce_float(_cell_at(cells, columns.get("dist"))),
                "tomtom_avg_sec": _time_to_sec(_cell_at(cells, columns.get("tomtom_avg"))),
                "model_output_sec": _time_to_sec(_cell_at(cells, columns.get("model_output"))),
                "tomtom_low_sec": _time_to_sec(_cell_at(cells, columns.get("tomtom_low"))),
                "tomtom_high_sec": _time_to_sec(_cell_at(cells, columns.get("tomtom_high"))),
            }
        )
    return rows


def _canonical_row_headers(cells, aliases: dict[str, set[str]]) -> dict[str, int]:
    """Canonicalize an arbitrary worksheet row into {canonical_name: column_index}."""
    headers: dict[str, int] = {}
    for index, value in enumerate(cells):
        normalized = _normalize_header(value)
        if not normalized:
            continue
        canonical = next(
            (key for key, candidates in aliases.items() if normalized in {_normalize_header(item) for item in candidates}),
            None,
        )
        if canonical is not None:
            headers.setdefault(canonical, index)
    return headers


def _cell_at(cells, index: int | None):
    if index is None or index >= len(cells):
        return None
    return cells[index]


def _time_to_sec(value) -> float | None:
    """Coerce a cell to seconds. Accepts a ``datetime.time``/``datetime.datetime``
    (e.g. an Excel mm:ss cell) or a plain numeric second value."""
    if isinstance(value, (datetime.time, datetime.datetime)):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000.0
    return _coerce_float(value)


def _parse_route_id(measurement: str) -> str:
    """Extract the numeric route id from '1: RoeHwy,MurdochDr_NB' style strings."""
    match = re.match(r"^\s*(\d+)\s*:", measurement)
    if match:
        return match.group(1)
    return measurement.strip()


def _parse_route_name(measurement: str) -> str:
    """Extract the route name (everything after the leading 'id:' prefix)."""
    match = re.match(r"^\s*\d+\s*:\s*(.+)$", measurement)
    return match.group(1).strip() if match else measurement.strip()


def _headers_for_sheet(ws) -> dict[str, int]:
    first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    headers: dict[str, int] = {}
    for index, value in enumerate(first_row):
        normalized = _clean_string(value).lower()
        if normalized:
            headers[normalized] = index
    return headers


def _canonical_headers_for_sheet(ws, aliases: dict[str, set[str]]) -> dict[str, int]:
    first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    headers: dict[str, int] = {}
    for index, value in enumerate(first_row):
        normalized = _normalize_header(value)
        if not normalized:
            continue
        canonical = next(
            (key for key, candidates in aliases.items() if normalized in {_normalize_header(item) for item in candidates}),
            normalized,
        )
        headers.setdefault(canonical, index)
    return headers


def _row_as_dict(headers: dict[str, int], row: tuple) -> dict[str, object]:
    return {
        header: row[index] if index < len(row) else None
        for header, index in headers.items()
    }


def _normalize_period(value: object) -> str:
    text = _clean_string(value).upper()
    if text in {"AM", "PM"}:
        return text
    return ""


def _period_from_time_range(value: object) -> str:
    text = _clean_string(value).replace(":", "").replace("-", "")
    if not text:
        return ""
    if text.startswith(("08", "09", "900", "0800", "0900")):
        return "AM"
    if text.startswith(("17", "18", "1700", "1800")):
        return "PM"
    return ""


def _parse_vissim_movement(value: object) -> tuple[str, str, str] | None:
    text = _clean_string(value)
    match = re.match(r"^\s*(\d+)\s*:\s*(.+?)\s+(-?[NSEW]-[NSEW]|Total)\s*$", text, re.IGNORECASE)
    if not match:
        return None
    node_id, node_name, movement = match.groups()
    return node_id, node_name.strip(), movement


def _normalize_movement(value: object) -> str:
    text = _clean_string(value).upper()
    normalized = text[1:] if text.startswith("-") else text
    return "Total" if normalized == "TOTAL" else normalized


def _normalize_header(value: object) -> str:
    return _clean_string(value).lower().replace(" ", "_")


def _clean_string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _coerce_int(value: object) -> int | None:
    parsed = _coerce_float(value)
    return int(parsed) if parsed is not None else None
